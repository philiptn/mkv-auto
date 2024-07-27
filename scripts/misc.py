import os
from datetime import datetime

# ANSI color codes
BLUE = '\033[34m'
RESET = '\033[0m'  # Reset to default terminal color
GREY = '\033[90m'
YELLOW = '\033[33m'
RED = '\033[31m'
GREEN = '\033[32m'

# Calculate max_workers as 80% of the available logical cores
max_workers = int(os.cpu_count() * 0.8)


def print_multi_or_single(amount, string):
    if amount == 1:
        return string
    elif amount > 1:
        return f"{string}s"
    else:
        return string


def get_timestamp():
    """Return the current UTC timestamp in the desired format."""
    current_time = datetime.utcnow()
    return current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]