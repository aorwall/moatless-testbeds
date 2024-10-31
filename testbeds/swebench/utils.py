"""
This file is adapted from SWE-bench:
https://github.com/princeton-nlp/SWE-bench/blob/main/swebench/harness/utils.py

MIT License

Copyright (c) 2023 Carlos E Jimenez, John Yang, Alexander Wettig, Shunyu Yao,
Kexin Pei, Ofir Press, Karthik R Narasimhan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import json
import logging
import re
from argparse import ArgumentTypeError
from pathlib import Path
from typing import cast, Optional

from datasets import Dataset, load_dataset

from testbeds.schema import SWEbenchInstance
from testbeds.swebench.constants import (
    NON_TEST_EXTS,
)

logger = logging.getLogger(__name__)

### MARK - Patch Correction
PATCH_PATTERN = re.compile(
    r"(?:diff[\w\_\.\ \/\-]+\n)?\-\-\-\s+a\/(?:.*?)\n\+\+\+\s+b\/(?:.*?)(?=diff\ |\-\-\-\ a\/|\Z)",
    re.DOTALL,
)
PATCH_FILE_PATTERN = re.compile(r"\-\-\-\s+a\/(?:.+)\n\+\+\+\s+b\/(?:.+)")
PATCH_HUNK_PATTERN = re.compile(
    r"\@\@\s+\-(\d+),(\d+)\s+\+(\d+),(\d+)\s+\@\@(.+?)(?=diff\ |\-\-\-\ a\/|\@\@\ \-|\Z)",
    re.DOTALL,
)


# Global variable to store the dataset
_SWEBENCH_DATASET = None


def load_swebench_instance(
    instance_id: str, name="princeton-nlp/SWE-bench_Lite", split="test"
) -> Optional[SWEbenchInstance]:
    # Try to load from individual instance file first
    instance_path = Path("/app/instances") / f"{instance_id}.json"
    if instance_path.exists():
        logger.debug(f"Loading instance from file: {instance_path.absolute()}")
        with instance_path.open("r", encoding="utf-8") as f:
            instance_data = json.load(f)
            return SWEbenchInstance(**instance_data)

    logger.debug(f"Loading instance from dataset: {name} {split}")
    # Fall back to original loading method
    if name.lower() in {"swe-bench", "swebench", "swe_be"}:
        name = "princeton-nlp/SWE-bench"
    elif name.lower() in {
        "swe-bench-lite",
        "swebench-lite",
        "swe_bench_lite",
        "swe-bench_lite",
        "lite",
    }:
        name = "princeton-nlp/SWE-bench_Lite"

    instances = load_swebench_dataset(name, split)

    for instance in instances:
        if instance.instance_id == instance_id:
            return instance

    logger.warning(f"Instance {instance_id} not found in dataset")
    return None


def load_swebench_dataset(
    name="princeton-nlp/SWE-bench", split="test"
) -> list[SWEbenchInstance]:
    """
    Load SWE-bench dataset from memory if available (only for princeton-nlp/SWE-bench),
    otherwise load from local JSON file if it exists,
    or from Hugging Face Datasets or local .json/.jsonl file
    """
    global _SWEBENCH_DATASET

    # Only use in-memory caching for princeton-nlp/SWE-bench
    if name == "princeton-nlp/SWE-bench_Lite":
        # If dataset is already in memory, return it
        if _SWEBENCH_DATASET is not None:
            return _SWEBENCH_DATASET

        # Update the local file path to use /app directory
        local_file = Path("/app/swebench_dataset.json")
        if local_file.exists():
            logger.info(f"Loading dataset from local file: {local_file.absolute()}")
            with local_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            _SWEBENCH_DATASET = [SWEbenchInstance(**instance) for instance in data]
            return _SWEBENCH_DATASET

    logger.debug(f"Loading dataset from Hugging Face Datasets: {name}")
    # For other datasets or if local file doesn't exist, proceed with original loading method
    # Load from local .json/.jsonl file
    if name.endswith(".json") or name.endswith(".jsonl"):
        return [
            SWEbenchInstance.model_validate(instance)
            for instance in json.loads(Path(name).read_text())
        ]

    # Load from Hugging Face Datasets
    if name.lower() in {"swe-bench", "swebench", "swe_bench"}:
        name = "princeton-nlp/SWE-bench"
    elif name.lower() in {
        "swe-bench-lite",
        "swebench-lite",
        "swe_bench_lite",
        "swe-bench_lite",
        "lite",
    }:
        name = "princeton-nlp/SWE-bench_Lite"
    dataset = cast(Dataset, load_dataset(name, split=split))

    instances = []
    for instance in dataset:
        if "FAIL_TO_PASS" in instance and isinstance(instance["FAIL_TO_PASS"], str):
            instance["fail_to_pass"] = eval(instance["FAIL_TO_PASS"])
            del instance["FAIL_TO_PASS"]
        if "PASS_TO_PASS" in instance and isinstance(instance["PASS_TO_PASS"], str):
            instance["pass_to_pass"] = eval(instance["PASS_TO_PASS"])
            del instance["PASS_TO_PASS"]

        instances.append(SWEbenchInstance(**instance))

    return instances


def get_first_idx(charlist):
    """Get index of first occurrence of "-" or "+" in charlist"""
    first_min = charlist.index("-") if "-" in charlist else len(charlist)
    first_plus = charlist.index("+") if "+" in charlist else len(charlist)
    return min(first_min, first_plus)


def get_last_idx(charlist):
    """Get index of last occurrence of "-" or "+" in charlist"""
    char_idx = get_first_idx(charlist[::-1])
    last_idx = len(charlist) - char_idx
    return last_idx + 1


def strip_content(hunk):
    """Remove trailing non +/- lines and trailing whitespace per line per hunk"""
    first_chars = list(map(lambda x: None if not len(x) else x[0], hunk.split("\n")))
    first_idx = get_first_idx(first_chars)
    last_idx = get_last_idx(first_chars)
    new_lines = list(map(lambda x: x.rstrip(), hunk.split("\n")[first_idx:last_idx]))
    new_hunk = "\n" + "\n".join(new_lines) + "\n"
    return new_hunk, first_idx - 1


def get_hunk_stats(pre_start, pre_len, post_start, post_len, hunk, total_delta):
    """Recalculate hunk start/end position and diff delta"""
    stats = {"context": 0, "added": 0, "subtracted": 0}
    hunk = hunk.split("\n", 1)[-1].strip("\n")
    for line in hunk.split("\n"):
        if line.startswith("-"):
            stats["subtracted"] += 1
        elif line.startswith("+"):
            stats["added"] += 1
        else:
            stats["context"] += 1
    context = stats["context"]
    added = stats["added"]
    subtracted = stats["subtracted"]
    pre_len = context + subtracted
    post_start = pre_start + total_delta
    post_len = context + added
    total_delta = total_delta + (post_len - pre_len)
    return pre_start, pre_len, post_start, post_len, total_delta


def extract_minimal_patch(model_patch):
    """
    Wrapper function that takes hunk and
    * Removes trailing non +/- lines and trailing whitespace per line per hunk
    * Recalculates hunk start/end position and diff delta
    * Returns new patch
    """
    model_patch = model_patch.lstrip("\n")
    new_patch = ""
    for patch in PATCH_PATTERN.findall(model_patch):
        total_delta = 0
        patch_header = PATCH_FILE_PATTERN.findall(patch)[0]
        if patch_header:
            new_patch += patch_header + "\n"
        for hunk in PATCH_HUNK_PATTERN.findall(patch):
            pre_start, pre_len, post_start, post_len, content = hunk
            pre_start, pre_len, post_start, post_len, content = list(
                map(lambda x: int(x) if x.isnumeric() else x, hunk)
            )
            content, adjust_pre_start = strip_content(content)
            pre_start += adjust_pre_start
            pre_start, pre_len, post_start, post_len, total_delta = get_hunk_stats(
                pre_start, pre_len, post_start, post_len, content, total_delta
            )
            new_patch += (
                f"@@ -{pre_start},{pre_len} +{post_start},{post_len} @@{content}"
            )
    return new_patch


def has_attribute_or_import_error(log_before):
    """
    Check to see if Attribute/Import-prefix is in log text

    Args:
        log_before (str): Validation log text before patch application
    """
    log_before = log_before.lower()

    if any([x in log_before for x in ["attribute", "import"]]):

        def get_lines_with_word(text, target_word):
            # Function to extract line(s) that contains target_word
            text, target_word = text.lower(), target_word.lower()
            lines, hits = text.split("\n")[::-1], []
            for line in lines:
                if target_word in line:
                    hits.append(line)
            return hits

        # Get line with Attribute/Import error
        lines_1 = get_lines_with_word(log_before, "attribute")
        lines_2 = get_lines_with_word(log_before, "import")
        lines_1 = " ".join(lines_1)
        lines_2 = " ".join(lines_2)

        if any([(x in lines_1 or x in lines_2) for x in ["error", "fail"]]):
            return True
    return False


def get_test_directives(instance: SWEbenchInstance) -> list:
    """
    Get test directives from the test_patch of a task instance

    Args:
        instance (dict): task instance
    Returns:
        directives (list): List of test directives
    """

    # Get test directives from test patch and remove non-test files
    diff_pat = r"diff --git a/.* b/(.*)"
    test_patch = instance.test_patch
    directives = re.findall(diff_pat, test_patch)
    directives = [
        d for d in directives if not any(d.endswith(ext) for ext in NON_TEST_EXTS)
    ]

    # For Django tests, remove extension + "tests/" prefix and convert slashes to dots (module referencing)
    if instance.repo == "django/django":
        directives_transformed = []
        for d in directives:
            d = d[: -len(".py")] if d.endswith(".py") else d
            d = d[len("tests/") :] if d.startswith("tests/") else d
            d = d.replace("/", ".")
            directives_transformed.append(d)
        directives = directives_transformed

    return directives


def str2bool(v):
    """
    Minor helper function to convert string to boolean
    """
    if isinstance(v, bool):
        return v
    if v.lower() in ("yes", "true", "t", "y", "1"):
        return True
    elif v.lower() in ("no", "false", "f", "n", "0"):
        return False
    else:
        raise ArgumentTypeError("Boolean value expected.")
