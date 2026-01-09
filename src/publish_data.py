import os
import pandas as pd
import matplotlib.pyplot as plt

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
    # Ensure all requested columns exist, filling missing with empty string
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    df = df[columns] 
    
    # --- 1. Calculate Dimensions ---
    row_height = 0.5
    header_height = 0.8
    padding = 0.5 
    if legend_text and legend_loc == 'bottom':
        padding += 0.6
        
    fig_height = (len(df) * row_height) + header_height + padding
    
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis('tight')
    ax.axis('off')
    
    # --- 2. Format Data ---
    cell_text = []
    for row in df.itertuples(index=False):
        formatted_row = []
        for cell in row:
            if isinstance(cell, (int, float)):
                # Auto-format numbers (float vs int)
                formatted_row.append(f"{cell:,.1f}" if isinstance(cell, float) else f"{cell:,.0f}")
            else:
                formatted_row.append(str(cell))
        cell_text.append(formatted_row)

    # --- 3. Draw Table ---
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

    # --- 4. Row Highlighting ---
    if highlight_last_rows > 0:
        total_rows = len(df)
        start_row = total_rows - highlight_last_rows + 1 
        
        for r in range(start_row, total_rows + 1):
            for c in range(len(columns)):
                cell = table[r, c]
                cell.set_facecolor('#e6e6e6')
                cell.set_text_props(weight='bold') 

    # --- 5. Add Legend ---
    if legend_text:
        if legend_loc == 'bottom':
            text_x, text_y = 0.98, 0.08
            va = 'bottom'
        else:
            text_x, text_y = 0.98, 0.95
            va = 'top'

        fig.text(
            text_x, text_y, legend_text, fontsize=9, 
            verticalalignment=va, horizontalalignment='right',
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="gray", alpha=0.9)
        )

    # --- 6. Add Footer ---
    if footer_text:
        fig.text(0.5, 0.02, footer_text, ha='center', fontsize=8, color='gray')

    save_path = os.path.join(output_dir, filename)
    
    # --- SAVE ---
    plt.savefig(save_path, bbox_inches='tight', pad_inches=save_padding, dpi=300)
    plt.close(fig)
    print(f"📸 Saved image: {save_path}")


def publish_dashboard(summary, output_dir):
    """
    Orchestrates the creation of all report images based on the summary dictionary.
    """
    if not summary:
        print("No summary data to publish.")
        return

    # 1. Global Stats
    if 'global_stats' in summary:
        g = summary['global_stats']
        # Robustly fetch the Year Range value
        year_range_val = next((item['Value'] for item in g if item['Metric'] == 'Year Range'), "Unknown")
        global_footer = f"{year_range_val} Strava activity data"
        
        print("\n=== GLOBAL STATS ===")
        for item in g: print(f"{item['Metric']:<20} : {item['Value']}")
        
        create_mpl_table(
            data=g, 
            columns=['Metric', 'Value'], 
            output_dir=output_dir,
            filename='1_global_stats.png', 
            footer_text=global_footer
        )

    # 2. Sport Stats
    if 'sport_ranking' in summary:
        print("\n=== SPORT TOTALS ===")
        s = summary['sport_ranking']
        s_table = [{'Sport': r['sport'], 'Count': r['count'], 'Total': r['total'], 'Unit': r['unit']} for r in s]
        
        create_mpl_table(
            data=s_table, 
            columns=['Sport', 'Count', 'Total', 'Unit'], 
            output_dir=output_dir,
            filename='2_sport_stats.png', 
            footer_text="2025 Strava activity data"
        )

    # 3. Bike Stats
    if 'bike_lifetime_miles' in summary:
        print("\n=== BIKE LIFETIME MILES ===")
        b = summary['bike_lifetime_miles']
        b_table = [{'Bike': r['bike'], 'Miles': r['miles']} for r in b]
        
        create_mpl_table(
            data=b_table, 
            columns=['Bike', 'Miles'], 
            output_dir=output_dir,
            filename='3_bike_stats.png', 
            footer_text="Source: Strava activity data"
        )

    # 4. Annual Stats
    if 'annual_totals' in summary:
        print("\n=== ANNUAL TOTALS ===")
        a = summary['annual_totals']
        a_table = [{
            'Year': str(r['year']), 
            'Bike (mi)': r['bike_miles'], 
            'Swim (m)': r['swim_meters'], 
            'Ski (ft)': r['ski_vert_ft']
        } for r in a]
        
        create_mpl_table(
            data=a_table, 
            columns=['Year', 'Bike (mi)', 'Swim (m)', 'Ski (ft)'], 
            output_dir=output_dir,
            filename='4_annual_stats.png'
        )

    # 5. Equity Analysis
    if 'equity_stats' in summary:
        print("\n=== EQUIVALENCY (SEq) ANALYSIS ===")
        eq = summary['equity_stats']
        breakdown = eq.get('breakdown', [])
        
        # Get 2025 actual bike miles from annual_totals if available
        annual = summary.get('annual_totals', [])
        bike_miles_2025 = next((item['bike_miles'] for item in annual if item['year'] == 2025), 0)
        
        if breakdown or bike_miles_2025 > 0:
            eq_table_data = []
            running_total = 0
            
            # A. Add Proxy Rows
            for row in breakdown:
                eq_table_data.append({
                    'Sport': row['source_sport'],
                    'Source Dist': f"{row['source_val']:,.0f} {row['source_unit']}",
                    'Total Miles': row['total_miles']
                })
                running_total += row['total_miles']
                print(f"{row['source_sport']:<15} {row['total_miles']:<10,.1f}")

            # B. Add Actual Bike Row
            eq_table_data.append({
                'Sport': 'Actual Bike',
                'Source Dist': '-',
                'Total Miles': bike_miles_2025
            })
            running_total += bike_miles_2025

            # C. Add Grand Total Row
            eq_table_data.append({
                'Sport': 'TOTAL',
                'Source Dist': '-',
                'Total Miles': running_total
            })

            legend_txt = (
                "Mileage Equivalents:\n"
                "• Snow sports: 1,000 vert ft = 1 bike mile\n"
                "• Swimming: 100 meters = 1 bike mile"
            )

            create_mpl_table(
                data=eq_table_data, 
                columns=['Sport', 'Source Dist', 'Total Miles'], 
                output_dir=output_dir,
                filename='5_equity_stats.png', 
                footer_text="2025 Strava activity data",
                legend_text=legend_txt,
                legend_loc='bottom',
                highlight_last_rows=2,
                fig_width=6.0,   
                save_padding=0.5
            )
        else:
            print("No 'SEq' or 'Eq' activities found.")
            
        # 6. Unmatched Log
        unmatched = eq.get('unmatched', [])
        if unmatched:
            print(f"\n⚠️  Unmatched Activities Logged: {len(unmatched)}")
        else:
            print("\n✅ No unmatched activities found.")