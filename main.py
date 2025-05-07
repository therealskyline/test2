import sys
import os

# Import the Flask app directly from the nested directory
sys.path.insert(0, os.path.abspath("."))

# Create a copy of the app file in the main directory
from realweb.final_website.app import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)