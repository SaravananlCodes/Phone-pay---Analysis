# PhonePe Pulse Dashboard

## Project Overview

This project is a Streamlit analytics dashboard built from PhonePe Pulse data.
It uses a local SQLite database (`phonepe_pulse.db`) and visualizes transaction,
user, device, geography, and state-level insights.

The current implementation is a single-file Streamlit app:

```text
app.py
data_Ext.py
phonepe_pulse.db
pulse/
```

## How To Run

From this folder:

```bash
streamlit run app.py
```

If Streamlit is launched manually with Python:

```bash
python -m streamlit run app.py
```

## Current Features

- Transaction KPIs
- Transaction value and count analysis
- Payment category breakdown
- Yearly and quarterly trends
- State-wise India choropleth map
- Top states, districts, and pincodes
- Users and device brand analysis
- Registered users and app opens by state
- Geo Explorer
- Automated business insights
- State Lab for deeper comparison and segmentation
- Sidebar filters for year, quarter, and state

## Current Database Tables

The current SQLite database contains these tables:

```text
aggregated_transaction
aggregated_user
map_transaction
map_user
top_transaction
top_user
```

The current database does not contain insurance tables.

## Important Notes

- Device brand data is available only up to the latest period present in
  `aggregated_user`.
- For newer quarters, the dashboard uses current registered-user data from
  `map_user` and the latest available device-brand snapshot.
- State filtering should affect the major visuals and KPI sections.
- The app is currently monolithic. It is not yet split into `pages/` and
  `utils/` modules.

## Useful Validation Checklist

Before submission or demo, check:

- `app.py` runs with Streamlit.
- Year, quarter, and state filters update the visuals.
- Users & Devices KPIs do not show zero incorrectly.
- Maps render correctly.
- State Lab works for one or more selected states.
- SQLite database exists in the project folder.
- The `pulse/` data folder is available if re-extraction is needed.

## Possible Future Improvements

- Split `app.py` into modular page files.
- Add a `requirements.txt`.
- Add a proper `README.md`.
- Add SQL Explorer page.
- Add insurance extraction and insurance tables if required.
- Add more downloadable reports.
- Add tests for SQL query helpers.

