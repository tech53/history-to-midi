#/usr/bin/env python3

import argparse
import os
import sqlite3
import shutil
import platform
from urllib.parse import urlparse
from midiutil import MIDIFile
from datetime import datetime

# --- Configuration ---
# You can adjust these parameters to change the musical output
NOTE_MAPPING_RANGE = (48, 84)  # MIDI notes C3 to C6
DEFAULT_BPM = 120
VELOCITY_BASE = 80 # Base note velocity
VELOCITY_MODULATION = 20 # How much visit count affects velocity
OUTPUT_FILENAME = "browser_history.mid"

def get_history_path(browser):
    """
    Gets the path to the browser history SQLite database file.
    
    Args:
        browser (str): The name of the browser ('chrome' or 'firefox').

    Returns:
        str: The full path to the history file.
        
    Raises:
        ValueError: If the browser is not supported.
        FileNotFoundError: If the history file cannot be found.
    """
    system = platform.system()
    if browser == 'chrome':
        if system == 'Darwin':  # macOS
            path = os.path.expanduser('~/Library/Application Support/Google/Chrome/Default/History')
        elif system == 'Windows':
            path = os.path.expanduser('~\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\History')
        elif system == 'Linux':
            path = os.path.expanduser('~/.config/google-chrome/Default/History')
        else:
            raise NotImplementedError(f"Unsupported OS for Chrome: {system}")
    elif browser == 'firefox':
        # Firefox history is in a 'places.sqlite' file within a profile folder.
        # This finds the most recently modified profile.
        if system == 'Darwin':
            base_path = os.path.expanduser('~/Library/Application Support/Firefox/Profiles/')
        elif system == 'Windows':
            base_path = os.path.expanduser('~\\AppData\\Roaming\\Mozilla\\Firefox\\Profiles\\')
        elif system == 'Linux':
            base_path = os.path.expanduser('~/.mozilla/firefox/')
        else:
            raise NotImplementedError(f"Unsupported OS for Firefox: {system}")

        profiles = [d for d in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, d))]
        latest_profile = max(profiles, key=lambda p: os.path.getmtime(os.path.join(base_path, p)))
        path = os.path.join(base_path, latest_profile, 'places.sqlite')
    else:
        raise ValueError("Unsupported browser. Please choose 'chrome' or 'firefox'.")

    if not os.path.exists(path):
        raise FileNotFoundError(f"History file not found at: {path}")
        
    return path

def fetch_history_data(db_path, browser):
    """
    Fetches browsing history from the SQLite database.
    Copies the file first to avoid database lock errors.

    Args:
        db_path (str): Path to the history database.
        browser (str): The name of the browser.

    Returns:
        list: A list of tuples, where each tuple contains (visit_time, url, visit_count).
    """
    # Create a temporary copy to avoid the "database is locked" error
    temp_db_path = "temp_history.sqlite"
    shutil.copy2(db_path, temp_db_path)
    
    conn = sqlite3.connect(temp_db_path)
    cursor = conn.cursor()

    # The query differs slightly between Chrome and Firefox
    if browser == 'chrome':
        # Chrome uses microseconds since 1601-01-01
        query = """
            SELECT
                (visits.visit_time / 1000000) - 11644473600 AS visit_unix_time,
                urls.url,
                urls.visit_count
            FROM urls
            JOIN visits ON urls.id = visits.url
            ORDER BY visit_unix_time ASC;
        """
    else: # firefox
        # Firefox uses microseconds since 1970-01-01 (Unix epoch)
        query = """
            SELECT
                moz_historyvisits.visit_date / 1000000 AS visit_unix_time,
                moz_places.url,
                moz_places.visit_count
            FROM moz_places
            JOIN moz_historyvisits ON moz_places.id = moz_historyvisits.place_id
            ORDER BY visit_unix_time ASC;
        """
        
    try:
        cursor.execute(query)
        history_data = cursor.fetchall()
    finally:
        conn.close()
        os.remove(temp_db_path) # Clean up the temporary file

    return history_data

def map_data_to_midi(history_data, bpm):
    """
    Maps browsing history data to a 4-voice MIDI sequence.

    Args:
        history_data (list): The list of (timestamp, url, visit_count) tuples.
        bpm (int): Beats per minute for the sequence.

    Returns:
        MIDIFile: A MIDIFile object ready to be written to a file.
    """
    num_voices = 4
    midi_file = MIDIFile(num_voices, removeDuplicates=True)
    
    for i in range(num_voices):
        midi_file.addTrackName(i, 0, f"Voice {i+1}")
        midi_file.addTempo(i, 0, bpm)

    if not history_data:
        print("No history data to process.")
        return midi_file

    last_time = history_data[0][0]
    
    # Normalize visit counts for velocity mapping
    max_visit_count = max(item[2] for item in history_data) if history_data else 1
    if max_visit_count == 0: max_visit_count = 1

    for timestamp, url, visit_count in history_data:
        try:
            parsed_url = urlparse(url)
            domain = parsed_url.netloc
            path = parsed_url.path
        except Exception:
            continue # Skip malformed URLs

        # --- Musical Mapping Logic ---

        # 1. Assign to a voice (track) based on domain hash
        voice_index = hash(domain) % num_voices

        # 2. Determine Pitch based on URL components
        # Hash of the domain sets the base note, path length shifts it
        note_range_size = NOTE_MAPPING_RANGE[1] - NOTE_MAPPING_RANGE[0]
        base_note = NOTE_MAPPING_RANGE[0] + (abs(hash(domain)) % (note_range_size // 2))
        path_shift = len(path) % 12 # Chromatic shift based on path length
        pitch = base_note + path_shift
        
        # Ensure pitch is within the desired range
        pitch = max(NOTE_MAPPING_RANGE[0], min(NOTE_MAPPING_RANGE[1], pitch))

        # 3. Determine Start Time and Duration based on timestamps
        time_delta = timestamp - last_time
        start_time = timestamp - history_data[0][0] # Time since the first event
        
        # Convert time delta to musical beats
        # Let's say a 10-second gap is a whole note (4 beats)
        duration = max(0.25, min(4.0, time_delta / 2.5)) # Duration in beats

        # 4. Determine Velocity (loudness) based on visit count
        normalized_visits = visit_count / max_visit_count
        velocity = int(VELOCITY_BASE + (normalized_visits * VELOCITY_MODULATION))
        velocity = max(0, min(127, velocity))

        # Add the note to the MIDI track
        midi_file.addNote(
            track=voice_index,
            channel=0,
            pitch=pitch,
            time=start_time,
            duration=duration,
            volume=velocity
        )
        
        last_time = timestamp

    return midi_file

def main():
    """Main function to parse arguments and run the script."""
    parser = argparse.ArgumentParser(
        description="Convert browser history into a polyphonic MIDI sequence.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        '--browser',
        required=True,
        choices=['chrome', 'firefox'],
        help="Specify the browser whose history you want to use."
    )
    parser.add_argument(
        '--bpm',
        type=int,
        default=DEFAULT_BPM,
        help=f"Set the tempo in beats per minute (BPM). Default is {DEFAULT_BPM}."
    )
    args = parser.parse_args()

    print(f"Starting process for {args.browser} at {args.bpm} BPM...")

    try:
        # Step 1: Find the history file
        print("Locating browser history file...")
        history_db_path = get_history_path(args.browser)
        print(f"Found history at: {history_db_path}")

        # Step 2: Fetch the data
        print("Fetching history data (this may take a moment)...")
        history_data = fetch_history_data(history_db_path, args.browser)
        print(f"Found {len(history_data)} history entries.")

        # Step 3: Map data to MIDI
        print("Composing MIDI sequence...")
        midi_sequence = map_data_to_midi(history_data, args.bpm)

        # Step 4: Write the MIDI file
        with open(OUTPUT_FILENAME, "wb") as output_file:
            midi_sequence.writeFile(output_file)
        
        print("\n----------------------------------------------------")
        print(f"Success! MIDI file created: {OUTPUT_FILENAME}")
        print("----------------------------------------------------")

    except (ValueError, FileNotFoundError, NotImplementedError) as e:
        print(f"\nError: {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")

if __name__ == '__main__':
    main()
