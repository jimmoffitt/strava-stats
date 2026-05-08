"""
tests/test_process.py — Unit tests for process_data aggregation logic.

Placeholder test suite; currently covers a stub for calculate_annual_stats.
Intended to grow alongside process_data.py as aggregation helpers mature.
Run with: pytest tests/
"""
import pandas as pd
from process_data import calculate_annual_stats

def test_calculate_annual_stats():
    # 1. Setup dummy data
    mock_data = pd.DataFrame({
        'miles': [10, 20, 30],
        'year': [2025, 2025, 2025]
    })
    
    # 2. Run your function
    result = calculate_annual_stats(mock_data)
    
    # 3. Assert the result is correct
    assert result == 60