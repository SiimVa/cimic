# cimic

This project analyzes cadastral data within a specified area defined by 4 MGRS coordinates.

## Requirements

Install the required packages:
```
pip install geopandas pyogrio shapely pyproj mgrs pandas folium streamlit
```

## Usage

Run the Streamlit app:
```
streamlit run app.py
```

The app will open in your browser. Enter the 4 MGRS coordinates for the analysis area, then click "Käivita analüüs" to perform the analysis.

## Outputs

- **CSV Download**: A table of cadastral units with their ownership forms and addresses.
- **Data Tables**: Summaries of ownership forms by count and area.
- **Interactive Map**: An embedded Folium map showing the analysis area and cadastral units colored by ownership type.