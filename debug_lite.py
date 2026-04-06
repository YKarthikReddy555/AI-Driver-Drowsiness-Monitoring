
import os
import traceback
import json
import sqlite3
import sys

# Mock common things if needed
sys.path.append(os.getcwd())

import utils.database as db
from utils.db_wrapper import DBWrapper

# Ensure environment variables are loaded
os.environ['DATABASE_URL'] = 'postgresql://neondb_owner:npg_dMtqJ4ojr2Wb@ep-purple-sea-annmq9wp-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require'

def test():
    try:
        logger = db.EventLogger()
        print("Connected to database.")
        
        print("\nTesting counts...")
        org_count = logger.get_all_organizations_count()
        print(f"Orgs Count: {org_count}")
        
        driver_count = logger.get_all_drivers_count()
        print(f"Drivers Count: {driver_count}")
        
        print("\nTesting Organisations list...")
        orgs = logger.get_all_organisations()
        print(f"Fetched {len(orgs)} orgs")
        if orgs:
            print("First org:", orgs[0])
            # Test JSON serialization
            try:
                json.dumps(orgs)
                print("Organisations are JSON serializable")
            except Exception as je:
                print(f"!!! JSON ERROR (Orgs): {je}")
            
        print("\nTesting Drivers list...")
        drivers = logger.get_all_drivers()
        print(f"Fetched {len(drivers)} drivers")
        if drivers:
            print("First driver:", drivers[0])
            try:
                json.dumps(drivers)
                print("Drivers are JSON serializable")
            except Exception as je:
                print(f"!!! JSON ERROR (Drivers): {je}")
                
        print("\nTesting Notifications...")
        # Assuming we have a driver ID from the previous test
        if drivers:
            first_id = drivers[0]['id']
            notifs = logger.get_notifications(first_id)
            print(f"Fetched {len(notifs)} notifications for id {first_id}")
            try:
                json.dumps(notifs)
                print("Notifications are JSON serializable")
            except Exception as je:
                print(f"!!! JSON ERROR (Notifs): {je}")

    except Exception:
        print("\n!!! SYSTEM ERROR !!!")
        traceback.print_exc()

if __name__ == "__main__":
    test()
