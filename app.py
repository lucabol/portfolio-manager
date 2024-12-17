import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
from os import environ
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token, credentials as google_credentials
from google.auth.transport import requests
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
import os
import json
import csv
from functools import wraps
import pathlib
import io
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from time import time

app = Flask(__name__)
# Configure from environment variables
app.config.update(
    SECRET_KEY=environ.get('FLASK_SECRET_KEY'),
    GOOGLE_CLIENT_ID=environ.get('GOOGLE_CLIENT_ID'),
    GOOGLE_CLIENT_SECRET=environ.get('GOOGLE_CLIENT_SECRET')
)

# Ensure data directory exists for temporary files
pathlib.Path("data").mkdir(exist_ok=True)

SCOPES = [
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'openid',
    'https://www.googleapis.com/auth/drive.file'
]

CACHE_DURATION = 60  # 1 minute
portfolio_cache = {}
MARKET_DATA_CACHE_DURATION = 300  # 5 minutes
market_data_cache = {}
save_executor = ThreadPoolExecutor(max_workers=1)  # Single worker for sequential saves

def get_drive_service():
    """Create and return Google Drive API service"""
    if 'credentials' not in session:
        return None
    credentials = google_credentials.Credentials(**session['credentials'])
    return build('drive', 'v3', credentials=credentials)

def find_or_create_portfolio_file(service):
    """Find or create the portfolio file in user's Google Drive"""
    # Search for existing portfolio file
    results = service.files().list(
        q="name='portfolio.csv' and trashed=false",
        spaces='drive',
        fields='files(id, name)'
    ).execute()
    files = results.get('files', [])
    
    if files:
        return files[0]['id']
    
    # Create new file if it doesn't exist
    file_metadata = {
        'name': 'portfolio.csv',
        'mimeType': 'text/csv'
    }
    
    # Create empty CSV content
    csv_buffer = io.StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=['ticker', 'quantity'])
    writer.writeheader()
    
    media = MediaIoBaseUpload(
        io.BytesIO(csv_buffer.getvalue().encode()),
        mimetype='text/csv',
        resumable=True
    )
    
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()
    
    return file.get('id')

def get_stock_info(ticker):
    """Get stock price and dividend yield from Yahoo Finance"""
    current_time = time()
    
    # Check cache first
    if ticker in market_data_cache:
        data, timestamp = market_data_cache[ticker]
        if current_time - timestamp < MARKET_DATA_CACHE_DURATION:
            return data
    
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        price = info.get('currentPrice', 'N/A')
        div_yield = info.get('dividendYield', 0)  # Default to 0 if no dividend
        
        # Ensure price is a number or 'N/A'
        if price != 'N/A':
            try:
                price = float(price)
            except (ValueError, TypeError):
                price = 'N/A'
                
        data = {
            'price': price,
            'dividend_yield': div_yield if div_yield is not None else 0
        }
        
        # Cache the result
        market_data_cache[ticker] = (data, current_time)
        return data
    except Exception as e:
        print(f"Error getting stock info for {ticker}: {e}")
        data = {
            'price': 'N/A',
            'dividend_yield': 0
        }
        market_data_cache[ticker] = (data, current_time)
        return data

def get_cached_portfolio(user_email):
    """Get cached portfolio or read from drive if cache expired"""
    current_time = time()
    if user_email in portfolio_cache:
        cached_data, timestamp = portfolio_cache[user_email]
        if current_time - timestamp < CACHE_DURATION:
            return cached_data
    
    # Cache miss or expired, read from drive
    portfolio = read_portfolio_from_drive()
    portfolio_cache[user_email] = (portfolio, current_time)
    return portfolio

def read_portfolio_from_drive():
    """Read portfolio from Google Drive without market data"""
    if 'user' not in session:
        return []
    
    service = get_drive_service()
    if not service:
        return []
    
    try:
        file_id = find_or_create_portfolio_file(service)
        request = service.files().get_media(fileId=file_id)
        
        file_content = io.BytesIO()
        downloader = MediaIoBaseDownload(file_content, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
            
        file_content.seek(0)
        csv_content = io.StringIO(file_content.read().decode())
        reader = csv.DictReader(csv_content)
        return list(reader)
        
    except Exception as e:
        print(f"Error reading portfolio: {e}")
        return []

def save_portfolio_to_drive(user_email, basic_portfolio, credentials):
    """Save portfolio to Google Drive in background"""
    try:
        service = build('drive', 'v3', credentials=google_credentials.Credentials(**credentials))
        if not service:
            return
        
        # Prepare CSV content
        csv_buffer = io.StringIO()
        writer = csv.DictWriter(csv_buffer, fieldnames=['ticker', 'quantity'])
        writer.writeheader()
        writer.writerows(basic_portfolio)
        
        file_id = find_or_create_portfolio_file(service)
        media = MediaIoBaseUpload(
            io.BytesIO(csv_buffer.getvalue().encode()),
            mimetype='text/csv',
            resumable=True
        )
        
        service.files().update(
            fileId=file_id,
            media_body=media
        ).execute()
        
    except Exception as e:
        print(f"Error saving portfolio: {e}")

def save_portfolio(portfolio):
    """Save portfolio to Google Drive"""
    if 'user' not in session:
        return
    
    # Filter out market data, keep only ticker and quantity
    basic_portfolio = [
        {'ticker': p['ticker'], 'quantity': p['quantity']} 
        for p in portfolio
    ]
    
    # Save to drive in background
    save_executor.submit(save_portfolio_to_drive, 
                        session['user']['email'], 
                        basic_portfolio,
                        session['credentials'])

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def add_market_data(portfolio):
    """Add market data to portfolio positions"""
    with ThreadPoolExecutor(max_workers=10) as executor:
        ticker_data = {
            position['ticker']: future 
            for position in portfolio
            if (future := executor.submit(get_stock_info, position['ticker']))
        }
        
        # Update portfolio with market data
        for position in portfolio:
            market_data = ticker_data[position['ticker']].result()
            position.update(market_data)
            # Calculate total value
            try:
                price = float(market_data['price'])
                quantity = float(position['quantity'])
                position['total_value'] = price * quantity
            except (ValueError, TypeError):
                position['total_value'] = 'N/A'
    
    return portfolio

def read_portfolio():
    """Read portfolio and add market data"""
    portfolio = read_portfolio_from_drive()
    return add_market_data(portfolio)

@app.route('/')
@login_required
def index():
    portfolio = read_portfolio()  # Use read_portfolio instead of get_cached_portfolio
    return render_template('index.html', portfolio=portfolio)

@app.route('/login')
def login():
    if 'user' in session:
        return redirect(url_for('index'))
    
    # Create client config from environment variables
    client_config = {
        "web": {
            "client_id": app.config['GOOGLE_CLIENT_ID'],
            "client_secret": app.config['GOOGLE_CLIENT_SECRET'],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [url_for('oauth2callback', _external=True)]
        }
    }
    
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=url_for('oauth2callback', _external=True)
    )
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    client_config = {
        "web": {
            "client_id": app.config['GOOGLE_CLIENT_ID'],
            "client_secret": app.config['GOOGLE_CLIENT_SECRET'],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [url_for('oauth2callback', _external=True)]
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=session['state'],
        redirect_uri=url_for('oauth2callback', _external=True)
    )
    
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials
    
    # Save credentials in session
    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    
    id_info = id_token.verify_oauth2_token(
        credentials.id_token, requests.Request(), app.config['GOOGLE_CLIENT_ID']
    )
    
    session['user'] = id_info
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/add', methods=['POST'])
@login_required
def add_position():
    ticker = request.form.get('ticker').upper()
    new_quantity = float(request.form.get('quantity'))
    
    portfolio = get_cached_portfolio(session['user']['email'])
    
    # Check if ticker already exists
    for position in portfolio:
        if position['ticker'] == ticker:
            # Update quantity
            position['quantity'] = str(float(position['quantity']) + new_quantity)
            break
    else:
        # Add new position
        portfolio.append({
            'ticker': ticker,
            'quantity': str(new_quantity)
        })
    
    # Save basic data
    save_portfolio(portfolio)
    
    # Add market data and return
    portfolio = add_market_data(portfolio)
    return render_template('tbody.html', portfolio=portfolio)

@app.route('/edit/<ticker>', methods=['PUT'])
@login_required
def edit_position(ticker):
    quantity = request.form.get('quantity')
    portfolio = get_cached_portfolio(session['user']['email'])
    
    for position in portfolio:
        if position['ticker'] == ticker:
            position['quantity'] = quantity
            break
    
    save_portfolio(portfolio)
    return quantity

@app.route('/delete/<ticker>', methods=['DELETE'])
@login_required
def delete_position(ticker):
    portfolio = get_cached_portfolio(session['user']['email'])
    portfolio = [p for p in portfolio if p['ticker'] != ticker]
    save_portfolio(portfolio)
    return ''

@app.template_filter('portfolio_total')
def portfolio_total(portfolio):
    """Calculate total value of portfolio, excluding invalid entries"""
    total = 0
    for position in portfolio:
        try:
            if position['total_value'] != 'N/A' and position['total_value'] is not None:
                total += float(position['total_value'])
        except (ValueError, TypeError):
            continue
    return total

@app.template_filter('number_format')
def number_format(value, show_decimals=False):
    """Format number with commas and optional decimal places"""
    try:
        if show_decimals:
            return "{:,.2f}".format(float(value))
        else:
            return "{:,.0f}".format(float(value))
    except (ValueError, TypeError):
        return "N/A"

if __name__ == '__main__':
    app.run(debug=True) 