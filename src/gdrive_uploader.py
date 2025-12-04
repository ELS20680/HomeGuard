import os
import pickle
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# If modifying these scopes, delete the file token.pickle.
SCOPES = ['https://www.googleapis.com/auth/drive.file']

class GoogleDriveUploader:
    def __init__(self, credentials_file='credentials.json'):
        self.credentials_file = credentials_file
        self.service = None
        self.folder_id = None
        self.authenticate()
        
    def authenticate(self):
        """Authenticate and create Drive service"""
        creds = None
        token_file = 'token.pickle'
        
        # Load existing token if available
        if os.path.exists(token_file):
            with open(token_file, 'rb') as token:
                creds = pickle.load(token)
        
        # If no valid credentials, authenticate
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                print("[GDRIVE] Refreshing expired credentials...")
                creds.refresh(Request())
            else:
                print("[GDRIVE] Starting authentication flow...")
                print("[GDRIVE] A browser window will open - please authorize the app")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)
                print("[GDRIVE] Authentication successful!")
            
            # Save credentials for next time
            with open(token_file, 'wb') as token:
                pickle.dump(creds, token)
        
        self.service = build('drive', 'v3', credentials=creds)
        print("[GDRIVE] Connected to Google Drive")
        
        # Get or create HomeGuardian folder
        self.folder_id = self._get_or_create_folder('HomeGuardian')
    
    def _get_or_create_folder(self, folder_name):
        """Get folder ID or create if doesn't exist"""
        # Search for folder
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = self.service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        items = results.get('files', [])
        
        if items:
            folder_id = items[0]['id']
            print(f"[GDRIVE] Using existing folder: {folder_name} (ID: {folder_id})")
            return folder_id
        else:
            # Create folder
            file_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = self.service.files().create(body=file_metadata, fields='id').execute()
            folder_id = folder.get('id')
            print(f"[GDRIVE] Created new folder: {folder_name} (ID: {folder_id})")
            return folder_id
    
    def upload_photo(self, local_path, photo_type='manual'):
        """
        Upload a photo to Google Drive
        
        Args:
            local_path: Path to the local photo file
            photo_type: 'manual' or 'motion' for naming
        
        Returns:
            Google Drive file ID if successful, None otherwise
        """
        try:
            if not os.path.exists(local_path):
                print(f"[GDRIVE ERROR] File not found: {local_path}")
                return None
            
            # Extract timestamp from filename
            filename = os.path.basename(local_path)
            
            # Create Drive filename with type prefix
            drive_filename = f"{photo_type}_{filename}"
            
            file_metadata = {
                'name': drive_filename,
                'parents': [self.folder_id]
            }
            
            media = MediaFileUpload(local_path, mimetype='image/jpeg', resumable=True)
            
            print(f"[GDRIVE] Uploading {drive_filename}...")
            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, webViewLink'
            ).execute()
            
            file_id = file.get('id')
            file_link = file.get('webViewLink', 'No link')
            
            print(f"[GDRIVE] Upload successful!")
            print(f"[GDRIVE] File: {drive_filename}")
            print(f"[GDRIVE] Link: {file_link}")
            
            return (file_id, file_link)
            
        except Exception as e:
            print(f"[GDRIVE ERROR] Upload failed: {e}")
            return None
    
    def delete_local_file(self, local_path):
        """Delete local file after successful upload"""
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
                print(f"[GDRIVE] Deleted local file: {local_path}")
                return True
        except Exception as e:
            print(f"[GDRIVE ERROR] Could not delete local file: {e}")
            return False

# Test function
if __name__ == "__main__":
    print("Testing Google Drive Uploader...")
    uploader = GoogleDriveUploader()
    print("Setup complete! The uploader is ready to use.")
