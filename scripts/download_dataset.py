#!/usr/bin/env python3
import json
import logging
import sys
from pathlib import Path

from testbeds.swebench.utils import load_swebench_dataset

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def download_dataset(force: bool = False) -> bool:
    output_path = Path("swebench_dataset.json")
    instances_dir = Path("instances")
    
    if output_path.exists() and instances_dir.exists() and not force:
        logger.info(f"Dataset already exists at {output_path.absolute()}")
        return True
    
    try:
        logger.info("Loading SWE-bench dataset...")
        instances = load_swebench_dataset()
        
        # Create instances directory
        instances_dir.mkdir(exist_ok=True)
        
        # Save individual instance files
        logger.info("Saving individual instance files...")
        for instance in instances:
            instance_path = instances_dir / f"{instance.instance_id}.json"
            with instance_path.open("w", encoding="utf-8") as f:
                json.dump(instance.dict(), f, indent=2)
        
        # Convert instances to JSON-serializable format and save full dataset
        instances_json = [instance.dict() for instance in instances]
        logger.info(f"Saving full dataset to {output_path.absolute()}")
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(instances_json, f, indent=2)
        
        logger.info(f"Successfully saved {len(instances)} instances")
        return True
        
    except Exception as e:
        logger.error(f"Failed to download dataset: {str(e)}")
        return False

def main():
    force = "--force" in sys.argv
    success = download_dataset(force)
    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main() 