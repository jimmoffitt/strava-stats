import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

# ==============================================================================
# VISUALIZATION ENGINE
# ==============================================================================

def create_mpl_table(data, columns, output_dir, filename, footer_text=None, legend_text=None, 
                     legend_loc='top', highlight_last_rows=0, 
                     fig_width=8, save_padding=0.1):
    """
    Generates a clean table image using Matplotlib.
    """
    if not data:
        print(f"⚠️ Warning: No data provided for {filename}")
        return

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    df = pd.DataFrame(data)
    # Ensure all requested columns exist
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    df = df[columns] 
    
    # Dimensions
    row_height = 0.5
    header_height = 0.8
    padding = 0.5 
    if legend_text and legend_loc == 'bottom': padding += 0.6
        
    fig_height = (len(df) * row_height) + header_height + padding
    
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis('tight')
    ax.axis('off')
    
    # Format Data
    cell_text = []
    for row in df.itertuples(index=False):
        formatted_row = []
        for cell in row:
            if isinstance(cell, (int, float)):
                formatted_row.append(f"{cell:,.1f}" if isinstance(cell, float) else f"{cell:,.0f}")
            else:
                formatted_row.append(str(cell))
        cell_text.append(formatted_row)

    # Draw Table
    table = ax.table(
        cellText=cell_text, 
        colLabels=columns, 
        loc='center', 
        cellLoc='center',
        colColours=['#e6e6e6'] * len(columns)
    )
    
    table.auto_set_font_size(False)
    table.set_fontsize(12)
    table.scale(1.2, 1.8)

    # Highlighting
    if highlight_last_rows > 0:
        total_rows = len(df)
        start_row = total_rows - highlight_last_rows + 1 
        for r in range(start_row, total_rows + 1):
            for c in range(len(columns)):
                cell = table[r, c]
                cell.set_facecolor('#e6e6e6')
                cell.set_text_props(weight='bold') 

    # Legend
    if legend_text:
        if legend_loc == 'bottom':
            text_x, text_y, va = 0.98, 0.08, 'bottom'
        else:
            text_x, text_y, va = 0.98, 0.95, 'top'

        fig.text(text_x, text_y, legend_text, fontsize=9, va=va, ha='right',
                 bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="gray", alpha=0.9))

    # Footer
    if footer_text:
        fig.text(0.5, 0.02, footer_text, ha='center', fontsize=8, color='gray')

    save_path = os.path.join(output_dir, filename)
    plt.savefig(save_path, bbox_inches='tight', pad_inches=save_padding, dpi=300)
    plt.close(fig)
    print(f"📸 Saved image: {save_path}")


# --- NEW CHARTS ---

def plot_annual_activities(df, output_dir):
    """
    Stacked bar chart of activity counts per year: Bike, Ski, Swim, Other.
    """
    if df.empty: return

    # 1. Map detailed types to broad categories
    def get_category(t):
        if t == 'Ride': return 'Bike'
        if t == 'Swim': return 'Swim'
        if t in ['AlpineSki', 'BackcountrySki', 'NordicSki', 'Snowboard']: return 'Ski'
        return 'Other'

    # Create a copy to avoid SettingWithCopy warnings
    plot_df = df.copy()
    plot_df['category'] = plot_df['final_type'].apply(get_category)
    
    # 2. Pivot: Index=Year, Columns=Category, Values=Count
    counts = plot_df.groupby(['year', 'category']).size().unstack(fill_value=0)
    
    # Ensure specific columns order if they exist
    desired_order = ['Bike', 'Ski', 'Swim', 'Other']
    cols = [c for c in desired_order if c in counts.columns]
    counts = counts[cols]

    # 3. Plot
    plt.figure(figsize=(10, 6))
    # We use Pandas built-in plot which handles stacking nicely
    counts.plot(kind='bar', stacked=True, figsize=(10, 6), colormap='viridis', edgecolor='black', alpha=0.85)
    
    plt.title('Activities per Year')
    plt.xlabel('Year')
    plt.ylabel('Number of Activities')
    plt.xticks(rotation=0)
    plt.grid(axis='y', linestyle='--', alpha=0.5)
    plt.legend(title=None)
    
    output_path = os.path.join(output_dir, 'chart_activities_per_year.png')
    plt.savefig(output_path, bbox_inches='tight', dpi=300)
    plt.close()
    print(f"📸 Saved chart: {output_path}")

def plot_cumulative_bike(df, output_dir):
    """
    Line graph of accumulated bike miles for the most recent completed year.
    """
    if df.empty: return

    # 1. Determine Target Year (Current Year - 1)
    # If today is Jan 2026, we want 2025.
    target_year = datetime.now().year - 1
    
    # Filter for Bike rides in that year
    bike_df = df[(df['year'] == target_year) & (df['final_type'] == 'Ride')].copy()
    
    if bike_df.empty:
        print(f"⚠️ No bike data found for {target_year}, skipping cumulative plot.")
        return

    # 2. Sort and Calculate Cumsum
    bike_df = bike_df.sort_values('start_date_local')
    bike_df['cumulative'] = bike_df['distance_miles'].cumsum()

    # 3. Plot
    plt.figure(figsize=(10, 6))
    plt.plot(bike_df['start_date_local'], bike_df['cumulative'], marker='', linewidth=2.5, color='#fc4c02') # Strava Orange
    
    # Fill area under line
    plt.fill_between(bike_df['start_date_local'], bike_df['cumulative'], color='#fc4c02', alpha=0.1)

    plt.title(f"Cumulative Bike Miles ({target_year})")
    plt.ylabel("Miles")
    plt.xlabel("Date")
    plt.grid(True, linestyle='--', alpha=0.5)
    
    # Format X-axis to show months nicely
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%b'))
    plt.gca().xaxis.set_major_locator(mdates.MonthLocator())
    
    # Annotate final value
    final_miles = bike_df['cumulative'].iloc[-1]
    final_date = bike_df['start_date_local'].iloc[-1]
    plt.annotate(f"{final_miles:,.0f} mi", 
                 (final_date, final_miles), 
                 textcoords="offset points", 
                 xytext=(-10, 10), 
                 ha='right',
                 fontweight='bold')

    output_path = os.path.join(output_dir, 'chart_cumulative_bike.png')
    plt.savefig(output_path, bbox_inches='tight', dpi=300)
    plt.close()
    print(f"📸 Saved chart: {output_path}")

def publish_dashboard(summary, df, output_dir):
    """
    Orchestrates the creation of all report images.
    Now accepts 'df' to generate charts.
    """
    if not summary:
        print("No summary data to publish.")
        return
        
    # --- Generate Charts ---
    # These rely on the raw dataframe, not the summary dict
    if df is not None and not df.empty:
        print("\n=== GENERATING CHARTS ===")
        plot_annual_activities(df, output_dir)
        plot_cumulative_bike(df, output_dir)

    # --- Generate Tables (Existing logic) ---
    # 1. Global Stats
    if 'global_stats' in summary:
        g = summary['global_stats']
        year_range_val = next((item['Value'] for item in g if item['Metric'] == 'Year Range'), "Unknown")
        create_mpl_table(g, ['Metric', 'Value'], output_dir, '1_global_stats.png', footer_text=f"{year_range_val} Strava data")

    # 2. Sport Stats
    if 'sport_ranking' in summary:
        s = summary['sport_ranking']
        s_table = [{'Sport': r['sport'], 'Count': r['count'], 'Total': r['total'], 'Unit': r['unit']} for r in s]
        create_mpl_table(s_table, ['Sport', 'Count', 'Total', 'Unit'], output_dir, '2_sport_stats.png')

    # 3. Bike Stats
    if 'bike_lifetime_miles' in summary:
        b = summary['bike_lifetime_miles']
        b_table = [{'Bike': r['bike'], 'Miles': r['miles']} for r in b]
        create_mpl_table(b_table, ['Bike', 'Miles'], output_dir, '3_bike_stats.png')

    # 4. Annual Stats
    if 'annual_totals' in summary:
        a = summary['annual_totals']
        a_table = [{'Year': str(r['year']), 'Bike (mi)': r['bike_miles'], 'Swim (m)': r['swim_meters'], 'Ski (ft)': r['ski_vert_ft']} for r in a]
        create_mpl_table(a_table, ['Year', 'Bike (mi)', 'Swim (m)', 'Ski (ft)'], output_dir, '4_annual_stats.png')

    # 5. Equity Analysis
    if 'equity_stats' in summary:
        eq = summary['equity_stats']
        breakdown = eq.get('breakdown', [])
        annual = summary.get('annual_totals', [])
        # Hardcoded check for 2025/Current year logic could be improved here, but keeping simple for now
        bike_miles_2025 = next((item['bike_miles'] for item in annual if item['year'] == 2025), 0)
        
        if breakdown or bike_miles_2025 > 0:
            eq_table_data = []
            running_total = 0
            for row in breakdown:
                eq_table_data.append({'Sport': row['source_sport'], 'Source Dist': f"{row['source_val']:,.0f} {row['source_unit']}", 'Total Miles': row['total_miles']})
                running_total += row['total_miles']
            eq_table_data.append({'Sport': 'Actual Bike', 'Source Dist': '-', 'Total Miles': bike_miles_2025})
            running_total += bike_miles_2025
            eq_table_data.append({'Sport': 'TOTAL', 'Source Dist': '-', 'Total Miles': running_total})

            legend_txt = "Mileage Equivalents:\n• Snow sports: 1,000 vert ft = 1 bike mile\n• Swimming: 100 meters = 1 bike mile"
            create_mpl_table(eq_table_data, ['Sport', 'Source Dist', 'Total Miles'], output_dir, '5_equity_stats.png', 
                             legend_text=legend_txt, legend_loc='bottom', highlight_last_rows=2, fig_width=6.0, save_padding=0.5)