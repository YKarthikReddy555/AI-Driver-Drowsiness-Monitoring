
import os
import traceback
import json
from decimal import Decimal

# Ensure environment variables are loaded
os.environ['DATABASE_URL'] = 'postgresql://neondb_owner:npg_dMtqJ4ojr2Wb@ep-purple-sea-annmq9wp-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require'

from app import logger

def test():
    try:
        print("Testing counts...")
        print(f"Orgs Count: {logger.get_all_organizations_count()}")
        print(f"Drivers Count: {logger.get_all_drivers_count()}")
        
        print("\nTesting Organisations list...")
        orgs = logger.get_all_organisations()
        print(f"Fetched {len(orgs)} orgs")
        if orgs:
            print("First org keys:", orgs[0].keys())
            # Test JSON serialization (common culprit for 500)
            json.dumps(orgs)
            print("Organisations are JSON serializable")
            
        print("\nTesting Drivers list...")
        drivers = logger.get_all_drivers()
        print(f"Fetched {len(drivers)} drivers")
        if drivers:
            print("First driver keys:", drivers[0].keys())
            json.dumps(drivers)
            print("Drivers are JSON serializable")
            
    except Exception as e:
        print("\n!!! ERROR DETECTED !!!")
        traceback.print_exc()

if __name__ == "__main__":
    test()
