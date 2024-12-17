# Portfolio Manager

A web application for managing stock portfolios with real-time market data using Yahoo Finance API. Features Google OAuth authentication and automatic data persistence to Google Drive.

## Features

- Google OAuth authentication
- Real-time stock data from Yahoo Finance
- Automatic portfolio saving to Google Drive
- Market data caching for improved performance
- Responsive UI with HTMX for dynamic updates

## Setup

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd portfolio-manager
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   # On Windows
   venv\Scripts\activate
   # On Mac/Linux
   source venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create a .env file with your credentials:
   ```
   FLASK_SECRET_KEY=your_secret_key
   GOOGLE_CLIENT_ID=your_google_client_id
   GOOGLE_CLIENT_SECRET=your_google_client_secret
   ```

5. Run the application:
   ```bash
   python app.py
   ```

The application will be available at http://localhost:5000

## Google OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project
3. Enable the Google Drive API
4. Configure the OAuth consent screen
5. Create OAuth 2.0 credentials
6. Add http://localhost:5000/oauth2callback to the authorized redirect URIs

## Usage

1. Log in with your Google account
2. Add stocks to your portfolio using their ticker symbols
3. View real-time prices and total portfolio value
4. Edit or delete positions as needed

## Security Notes

- Never commit the .env file
- Keep your Google OAuth credentials secure
- Reset credentials if they are ever exposed