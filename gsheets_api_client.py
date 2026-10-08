"""
gsheets_api_client.py
---------------------
Python client for the Google Sheets Apps Script API.
Fetches sessions for a moderator/date and returns CSV-compatible dicts.
"""

import requests
from datetime import date, datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from prism_config import GSHEETS_APPS_SCRIPT_URL


@dataclass
class GSheetsError(Exception):
    """Custom error for GSheets API failures."""
    code: str
    message: str
    status_code: int


class GSheetsAPIClient:
    """
    Client for the Apps Script Web App deployed on the master Google Sheet.
    
    Usage:
        client = GSheetsAPIClient("https://script.google.com/macros/s/SCRIPT_ID/exec")
        sessions = client.fetch_sessions("Nimith Shetty", date(2026, 1, 9))
    """
    
    def __init__(self, apps_script_url: str, timeout: int = 30):
        """
        Args:
            apps_script_url: Full URL of deployed Apps Script Web App
                           (e.g., https://script.google.com/macros/s/ABC123/exec)
            timeout: Request timeout in seconds
        """
        self.base_url = apps_script_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Zoom-Prism-CommandCenter/1.0',
            'Accept': 'application/json'
        })
    
    def fetch_sessions(self, moderator: str, target_date: Optional[date] = None) -> List[Dict[str, Any]]:
        """
        Fetch sessions for a moderator on a specific date.
        
        Args:
            moderator: Exact moderator name as appears in Combined!Moderator column
            target_date: Date to fetch (defaults to today)
            
        Returns:
            List of session dicts with exact CSV_FORMAT.md column keys
            
        Raises:
            GSheetsError: On API failure, empty results, or malformed response
        """
        if target_date is None:
            target_date = date.today()
        
        params = {
            'moderator': moderator,
            'date': target_date.isoformat()  # YYYY-MM-DD
        }
        
        try:
            resp = self.session.get(self.base_url, params=params, timeout=self.timeout)
        except requests.Timeout:
            raise GSheetsError('TIMEOUT', f'Request timed out after {self.timeout}s', 408)
        except requests.RequestException as e:
            raise GSheetsError('NETWORK_ERROR', f'Network error: {e}', 0)
        
        if resp.status_code != 200:
            raise GSheetsError(
                'HTTP_ERROR',
                f'Apps Script returned {resp.status_code}: {resp.text[:200]}',
                resp.status_code
            )
        
        try:
            data = resp.json()
        except ValueError:
            raise GSheetsError('INVALID_JSON', 'Response is not valid JSON', resp.status_code)
        
        if not data.get('success'):
            error_code = data.get('error', 'UNKNOWN')
            message = data.get('message', 'Unknown error')
            raise GSheetsError(error_code, message, resp.status_code)
        
        sessions = data.get('sessions', [])
        if not sessions:
            raise GSheetsError(
                'NO_SESSIONS',
                f'No sessions found for "{moderator}" on {target_date.isoformat()}',
                200
            )
        
        # Validate and normalize each session
        validated = []
        for s in sessions:
            if not s.get('Meeting Link') and s.get('Type') == 'PRISM':
                # PRISM sessions have empty Meeting Link - will be fetched later via Prism API
                pass
            elif not s.get('Meeting Link'):
                # Skip rows with no link at all
                continue
            validated.append(self._normalize_session(s))
        
        return validated
    
    def fetch_moderators(self, target_date: Optional[date] = None) -> List[str]:
        """
        Fetch list of moderators who have sessions on a given date.
        Used to populate the moderator dropdown in the sync dialog.
        
        Args:
            target_date: Date to check (defaults to today)
            
        Returns:
            Sorted list of moderator names (non-empty, deduplicated)
        """
        if target_date is None:
            target_date = date.today()
        
        params = {
            'action': 'moderators',
            'date': target_date.isoformat()
        }
        
        try:
            resp = self.session.get(self.base_url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise GSheetsError('NETWORK_ERROR', f'Network error: {e}', 0)
        
        if resp.status_code != 200:
            raise GSheetsError('HTTP_ERROR', f'Status {resp.status_code}', resp.status_code)
        
        try:
            data = resp.json()
        except ValueError:
            raise GSheetsError('INVALID_JSON', 'Response is not valid JSON', resp.status_code)
        
        if not data.get('success'):
            raise GSheetsError(data.get('error', 'UNKNOWN'), data.get('message', ''), resp.status_code)
        
        return data.get('moderators', [])

    def fetch_token(self) -> str:
        """
        Fetch the Prism API bearer token from the sheet's Config tab, via
        the Apps Script's `action=token` branch, so the token doesn't have
        to live in the app's code.

        Returns:
            The token string (never empty)

        Raises:
            GSheetsError: network/HTTP failure, non-JSON reply, the script
            reporting failure (e.g. Config tab missing or the cell empty),
            or a script that hasn't been updated with the token branch yet
            (it then falls through to the default session lookup and
            answers MISSING_PARAMS).
        """
        try:
            resp = self.session.get(self.base_url, params={'action': 'token'}, timeout=self.timeout)
        except requests.Timeout:
            raise GSheetsError('TIMEOUT', f'Request timed out after {self.timeout}s', 408)
        except requests.RequestException as e:
            raise GSheetsError('NETWORK_ERROR', f'Network error: {e}', 0)

        if resp.status_code != 200:
            raise GSheetsError('HTTP_ERROR', f'Status {resp.status_code}', resp.status_code)

        try:
            data = resp.json()
        except ValueError:
            raise GSheetsError('INVALID_JSON', 'Response is not valid JSON', resp.status_code)

        if not data.get('success'):
            raise GSheetsError(data.get('error', 'UNKNOWN'), data.get('message', ''), resp.status_code)

        token = str(data.get('token') or '').strip()
        if not token:
            raise GSheetsError('EMPTY_TOKEN', 'The script returned success but no token', resp.status_code)
        return token

    def _normalize_session(self, raw: Dict[str, Any]) -> Dict[str, str]:
        """Ensure all CSV_FORMAT.md columns exist with string values."""
        csv_columns = [
            'Type', 'Meeting Link', 'Program Name', 'Session Topic / Name',
            'Moderator Name', 'Session Start Time', 'Session End Time',
            'Meeting Code', 'Host Email', 'Host Password',
            'Prism Type', 'Prism Session ID', 'Prism Session Group ID'
        ]
        
        normalized = {}
        for col in csv_columns:
            val = raw.get(col, '')
            # Ensure we return a string, even if None
            normalized[col] = str(val).strip() if val is not None else ''
        
        return normalized


def create_client_from_settings() -> Optional[GSheetsAPIClient]:
    """
    Factory: create client from app settings.
    Falls back to the default URL from prism_config if not configured in settings.
    """
    from app_settings import get_settings
    settings = get_settings()
    url = settings.value('gsheets/apps_script_url', '')
    if not url:
        url = GSHEETS_APPS_SCRIPT_URL  # Use the default from prism_config
    return GSheetsAPIClient(url)