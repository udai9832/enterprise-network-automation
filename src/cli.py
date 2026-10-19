"""Small helpers shared by the command-line tools."""

import argparse


def positive_int(value):
    """argparse type for options such as --count and --timeout: a whole number of 1 or more."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or greater")
    return number
