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
    
    if output_path.exists() and not force:
        logger.info(f"Dataset already exists at {output_path.absolute()}")
        return True
        
    try:
        logger.info("Loading SWE-bench dataset...")
        instances = load_swebench_dataset()
        
        # Convert instances to JSON-serializable format
        instances_json = [instance.dict() for instance in instances]
        
        # Save to file
        logger.info(f"Saving dataset to {output_path.absolute()}")
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