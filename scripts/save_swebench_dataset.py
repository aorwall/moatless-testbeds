#!/usr/bin/env python3

import json
from pathlib import Path
from typing import cast

from datasets import Dataset, load_dataset

from testbed.swebench.utils import load_swebench_dataset
from testbed.schema import SWEbenchInstance

# Load the SWE-bench dataset
name = "princeton-nlp/SWE-bench"
dataset = cast(Dataset, load_dataset(name, split="test"))

# Create instances directory
instances_dir = Path("instances")
instances_dir.mkdir(exist_ok=True)

# Save each instance as a separate file
for instance in dataset:
    instance_id = instance["instance_id"]

    if "FAIL_TO_PASS" in instance and isinstance(instance["FAIL_TO_PASS"], str):
        instance["fail_to_pass"] = eval(instance["FAIL_TO_PASS"])
        del instance["FAIL_TO_PASS"]
    if "PASS_TO_PASS" in instance and isinstance(instance["PASS_TO_PASS"], str):
        instance["pass_to_pass"] = eval(instance["PASS_TO_PASS"])
        del instance["PASS_TO_PASS"]

    instance_path = instances_dir / f"{instance_id}.json"
    with instance_path.open("w", encoding="utf-8") as f:
        json.dump(instance, f, indent=2)
