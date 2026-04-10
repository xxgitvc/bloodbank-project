import os
 
MYSQL_SOCKET   = "/cloudsql/blood-bank-project-492616:us-central1:bloodbank-db"
MYSQL_USER     = os.environ.get("MYSQL_USER")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE")
 
SECRET_KEY = os.environ.get("SECRET_KEY")
 
DEBUG = True
 
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
 
PROJECT_ID   = "blood-bank-project-492616"
MAPS_API_KEY = os.environ.get("MAPS_API_KEY")
 
BASE_URL = os.environ.get("BASE_URL")
