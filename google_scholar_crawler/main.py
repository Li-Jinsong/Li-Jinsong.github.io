#!/usr/bin/env python3
"""
Robust Google Scholar Citation Scraper using the scholarly library.
This script scrapes citation data from a Google Scholar profile,
handles errors gracefully, uses proxies in CI environments,
and saves the data in a structured JSON format.
"""

import os
import json
import time
import signal
import sys
from datetime import datetime, timedelta
from functools import wraps
from scholarly import scholarly, ProxyGenerator

def timeout(seconds=3600):
    """
    Timeout decorator to prevent the script from hanging.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            def timeout_handler(signum, frame):
                raise TimeoutError(f"Function {func.__name__} timed out after {seconds} seconds")

            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(seconds)

            try:
                result = func(*args, **kwargs)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

            return result
        return wrapper
    return decorator

class RobustGoogleScholarScraper:
    """
    A robust scraper for Google Scholar profiles.
    """
    def __init__(self, scholar_id, output_dir='results'):
        self.scholar_id = scholar_id
        self.output_dir = output_dir
        self.is_github_actions = os.getenv('GITHUB_ACTIONS') == 'true'
        self.main_json_path = os.path.join(self.output_dir, 'gs_data.json')
        self._setup_scholarly()

    def _setup_scholarly(self):
        """
        Configures the scholarly library, using a proxy if running in GitHub Actions.
        """
        print("Setting up scholarly...")
        if self.is_github_actions:
            print("Running in GitHub Actions. Setting up proxy.")
            pg = ProxyGenerator()
            # Use free proxies, which is essential for CI environments
            success = pg.FreeProxies()
            if success:
                scholarly.use_proxy(pg)
                print("Successfully configured scholarly with free proxies.")
            else:
                print("Warning: Failed to configure free proxies. Scraping may fail.")
        else:
            print("Running locally. No proxy will be used.")

    def _load_existing_data(self):
        """Loads existing data from the main JSON file if it exists."""
        if os.path.exists(self.main_json_path):
            try:
                with open(self.main_json_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: Could not read existing data file. {e}")
        return None

    def _should_skip_scraping(self):
        """
        Determines if scraping should be skipped based on the last update time.
        Skips if data is less than 7 days old.
        """
        existing_data = self._load_existing_data()
        if not existing_data or 'updated' not in existing_data:
            return False

        try:
            last_updated = datetime.strptime(existing_data['updated'], '%Y-%m-%d %H:%M:%S')
            if datetime.now() - last_updated < timedelta(days=7):
                print("Skipping scrape: Existing data is less than 7 days old.")
                return True
        except (ValueError, TypeError):
             # If date format is wrong, proceed to scrape
            return False
        return False

    @timeout(3600) # 1-hour timeout for the entire scraping process
    def scrape(self):
        """
        Main method to perform the scraping operation.
        """
        print(f"Starting scrape for Google Scholar ID: {self.scholar_id}")

        if self.is_github_actions and self._should_skip_scraping():
             return self._load_existing_data()

        try:
            print("Searching for author by ID...")
            author = scholarly.search_author_id(self.scholar_id)

            print("Filling author details...")
            # Use a retry mechanism for filling author details
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    scholarly.fill(author, sections=['basics', 'indices', 'counts', 'publications'])
                    print("Author details filled successfully.")
                    break
                except Exception as e:
                    print(f"Attempt {attempt + 1} to fill author failed: {e}")
                    if attempt < max_attempts - 1:
                        time.sleep(10 * (attempt + 1)) # Exponential backoff
                    else:
                        raise e # Re-raise the last exception

            author['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # IMPORTANT: Preserve the original data structure
            author['publications'] = {p['author_pub_id']: p for p in author['publications']}

            os.makedirs(self.output_dir, exist_ok=True)
            print(f"Saving main data to {self.main_json_path}")
            with open(self.main_json_path, 'w') as f:
                json.dump(author, f, ensure_ascii=False, indent=2)

            self._save_shieldio_data(author)
            self._save_publication_shields(author)

            print("Scraping completed successfully.")
            return author

        except Exception as e:
            print(f"An error occurred during scraping: {e}")
            print("Scraping failed. Will attempt to use existing data as a fallback.")
            existing_data = self._load_existing_data()
            if existing_data:
                print("Successfully loaded fallback data.")
                return existing_data
            else:
                print("Error: Fallback data not available. Process failed.")
                return None

    def _save_shieldio_data(self, author_data):
        """Saves the main citation shield data."""
        shield_path = os.path.join(self.output_dir, 'gs_data_shieldsio.json')
        shield_data = {
            "schemaVersion": 1,
            "label": "citations",
            "message": str(author_data.get('citedby', 0)),
        }
        print(f"Saving citation shield data to {shield_path}")
        with open(shield_path, 'w') as f:
            json.dump(shield_data, f, ensure_ascii=False, indent=2)

    def _save_publication_shields(self, author_data):
        """Saves individual citation shields for each publication."""
        if 'publications' not in author_data or not isinstance(author_data['publications'], dict):
            return

        print(f"Saving shields for {len(author_data['publications'])} publications...")
        for pub_id, pub_info in author_data['publications'].items():
            pub_shield_path = os.path.join(self.output_dir, f"{pub_id}_shieldsio.json")
            pub_data = {
                "schemaVersion": 1,
                "label": "citations",
                "message": str(pub_info.get('num_citations', 0)),
            }
            with open(pub_shield_path, 'w') as f:
                json.dump(pub_data, f, ensure_ascii=False, indent=2)
        print("Publication shields saved.")


if __name__ == '__main__':
    if 'GOOGLE_SCHOLAR_ID' not in os.environ:
        print("Error: GOOGLE_SCHOLAR_ID environment variable not set.")
        sys.exit(1)

    scholar_id = os.environ['GOOGLE_SCHOLAR_ID']
    scraper = RobustGoogleScholarScraper(scholar_id=scholar_id)
    result_data = scraper.scrape()

    if result_data:
        print("\nProcess finished successfully.")
        sys.exit(0)
    else:
        print("\nProcess failed after all fallbacks.")
        sys.exit(1)