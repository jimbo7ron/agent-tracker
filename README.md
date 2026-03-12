# Agent Usage Tracker

A lightweight token and model usage tracker for OpenClaw agents.

## Features
- Logs token usage (In/Out) per agent and model.
- Stores data in a local SQLite database (`usage.db`).
- Generates daily usage reports.

## Usage
Log usage:
`python3 tracker.py log <agent> <model> <tokens_in> <tokens_out>`

Generate report:
`python3 tracker.py`

## Storage
Database is stored in the same directory as the script.
