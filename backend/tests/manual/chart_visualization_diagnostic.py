# backend/test_chart_viz.py

import asyncio
import json
from app.tools.chart_visualization_tool import ChartVisualizationTool
from app.tools.chart_visualization.chart_prepare import ChartPrepare
from app.tools.chart_visualization.data_visualization import DataVisualization
from app.tools.chart_visualization.python_execute import PythonExecute

async def test_chart_prepare():
    """Test the chart preparation step."""
    print("\n" + "="*50)
    print("TEST 1: Chart Preparation")
    print("="*50)
    
    # Test data: list of dicts
    data = [
        {"month": "Jan", "sales": 100, "profit": 30},
        {"month": "Feb", "sales": 150, "profit": 45},
        {"month": "Mar", "sales": 120, "profit": 35},
        {"month": "Apr", "sales": 200, "profit": 60},
        {"month": "May", "sales": 180, "profit": 55},
    ]
    
    result = await ChartPrepare.prepare(
        data=data,
        chart_type="bar",
        x_key="month",
        y_key="sales",
        title="Monthly Sales"
    )
    
    print(f"Success: {result.get('success')}")
    print(f"Chart Type: {result.get('chart_type')}")
    print(f"Chart Config: {json.dumps(result.get('chart_config'), indent=2)[:500]}...")
    
    return result

async def test_data_visualization():
    """Test the data visualization step."""
    print("\n" + "="*50)
    print("TEST 2: Data Visualization")
    print("="*50)
    
    # Use the prepared config from test 1
    chart_config = {
        "type": "bar",
        "data": {
            "labels": ["Jan", "Feb", "Mar", "Apr", "May"],
            "datasets": [{
                "label": "Sales",
                "data": [100, 150, 120, 200, 180]
            }]
        },
        "title": "Monthly Sales"
    }
    
    result = await DataVisualization.visualize(
        chart_config=chart_config,
        output_format="html",
        save_path="/workspace/charts/test_chart.html"
    )
    
    print(f"Success: {result.get('success')}")
    print(f"Path: {result.get('path')}")
    print(f"Format: {result.get('format')}")
    print(f"Chart Type: {result.get('chart_type')}")
    
    return result

async def test_chart_tool_direct():
    """Test the ChartVisualizationTool directly."""
    print("\n" + "="*50)
    print("TEST 3: Chart Tool (Direct Call)")
    print("="*50)
    
    tool = ChartVisualizationTool()
    
    data = [
        {"product": "A", "revenue": 500, "cost": 300},
        {"product": "B", "revenue": 700, "cost": 450},
        {"product": "C", "revenue": 300, "cost": 200},
        {"product": "D", "revenue": 900, "cost": 600},
    ]
    
    result = await tool.execute(
        data=data,
        chart_type="bar",
        title="Product Revenue",
        x_key="product",
        y_key="revenue",
        save_path="/workspace/charts/product_revenue.html"
    )
    
    print(f"Result: {result.get('output')}")
    print(f"Path: {result.get('path')}")
    print(f"Chart Type: {result.get('chart_type')}")
    
    return result

async def test_chart_tool_with_dict():
    """Test ChartVisualizationTool with dict data."""
    print("\n" + "="*50)
    print("TEST 4: Chart Tool (Dict Data)")
    print("="*50)
    
    tool = ChartVisualizationTool()
    
    data = {
        "Q1": 100,
        "Q2": 150,
        "Q3": 200,
        "Q4": 180
    }
    
    result = await tool.execute(
        data=data,
        chart_type="pie",
        title="Quarterly Revenue",
        save_path="/workspace/charts/quarterly_revenue.html"
    )
    
    print(f"Result: {result.get('output')}")
    print(f"Path: {result.get('path')}")
    print(f"Chart Type: {result.get('chart_type')}")
    
    return result

async def test_chart_tool_with_list_of_lists():
    """Test ChartVisualizationTool with list of lists."""
    print("\n" + "="*50)
    print("TEST 5: Chart Tool (List of Lists)")
    print("="*50)
    
    tool = ChartVisualizationTool()
    
    data = [
        ["Apple", 30],
        ["Banana", 45],
        ["Orange", 25],
        ["Grape", 60],
    ]
    
    result = await tool.execute(
        data=data,
        chart_type="pie",
        title="Fruit Distribution",
        save_path="/workspace/charts/fruit_distribution.html"
    )
    
    print(f"Result: {result.get('output')}")
    print(f"Path: {result.get('path')}")
    
    return result

async def test_python_execute():
    """Test Python execution for data processing."""
    print("\n" + "="*50)
    print("TEST 6: Python Execute")
    print("="*50)
    
    code = """
data = [
    {"month": "Jan", "sales": 100},
    {"month": "Feb", "sales": 150},
    {"month": "Mar", "sales": 120},
    {"month": "Apr", "sales": 200},
    {"month": "May", "sales": 180},
]

# Calculate statistics
total = sum(d["sales"] for d in data)
avg = total / len(data)
max_val = max(d["sales"] for d in data)
min_val = min(d["sales"] for d in data)

print(f"Total: {total}")
print(f"Average: {avg}")
print(f"Max: {max_val}")
print(f"Min: {min_val}")

# Create processed data
processed = {
    "total": total,
    "average": avg,
    "max": max_val,
    "min": min_val,
    "data": data
}
"""
    
    result = await PythonExecute.execute(code, timeout=10)
    
    print(f"Success: {result.get('success')}")
    print(f"Output: {result.get('output')}")
    if result.get('return_value'):
        print(f"Return Value: {result.get('return_value')}")
    if result.get('error'):
        print(f"Error: {result.get('error')}")
    
    return result

async def main():
    print("\n" + "="*60)
    print("CHART VISUALIZATION TEST SUITE")
    print("="*60)
    
    results = {}
    
    # Run all tests
    try:
        results["chart_prepare"] = await test_chart_prepare()
    except Exception as e:
        print(f"Test 1 failed: {e}")
        results["chart_prepare"] = {"success": False, "error": str(e)}
    
    try:
        results["data_visualization"] = await test_data_visualization()
    except Exception as e:
        print(f"Test 2 failed: {e}")
        results["data_visualization"] = {"success": False, "error": str(e)}
    
    try:
        results["chart_tool_direct"] = await test_chart_tool_direct()
    except Exception as e:
        print(f"Test 3 failed: {e}")
        results["chart_tool_direct"] = {"success": False, "error": str(e)}
    
    try:
        results["chart_tool_dict"] = await test_chart_tool_with_dict()
    except Exception as e:
        print(f"Test 4 failed: {e}")
        results["chart_tool_dict"] = {"success": False, "error": str(e)}
    
    try:
        results["chart_tool_list_of_lists"] = await test_chart_tool_with_list_of_lists()
    except Exception as e:
        print(f"Test 5 failed: {e}")
        results["chart_tool_list_of_lists"] = {"success": False, "error": str(e)}
    
    try:
        results["python_execute"] = await test_python_execute()
    except Exception as e:
        print(f"Test 6 failed: {e}")
        results["python_execute"] = {"success": False, "error": str(e)}
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = 0
    for name, result in results.items():
        status = "✅ PASS" if result.get("success") else "❌ FAIL"
        print(f"{name}: {status}")
        if result.get("success"):
            passed += 1
    
    print(f"\nTotal: {passed}/{len(results)} tests passed")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(main())