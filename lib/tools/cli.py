"""CLI Entrypoint

The main entrypoint for running tools script.
"""
import argparse

def cli():
    parser = argparse.ArgumentParser(
        description="Tools that help developers for running recurrent tasks on their XCP-ng sandbox."
    )

    parser.parse_args()
