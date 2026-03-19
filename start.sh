#!/bin/bash
echo "Starting WeatherEdge Bot..."
python api_server.py &
sleep 2
python main.py --mode paper
