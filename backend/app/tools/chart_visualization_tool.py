# backend/app/tools/chart_visualization_tool.py

import uuid
import os
import structlog
from typing import Dict, Any, Optional

from app.tools.registry import BaseTool, ToolSchema
from app.tools.chart_visualization.chart_prepare import ChartPrepare
from app.tools.chart_visualization.data_visualization import DataVisualization
from app.tools.chart_visualization.python_execute import PythonExecute

logger = structlog.get_logger("aether.tools.chart_viz")


class ChartVisualizationTool(BaseTool):
    """
    Complete chart generation pipeline.
    Given data, prepares, renders, and optionally saves a chart.
    """
    
    def _build_schema(self) -> ToolSchema:
        return ToolSchema(
            name="chart_visualization",
            description=(
                "Generate charts and visualizations from data. "
                "Takes raw data and creates bar, line, pie, scatter charts. "
                "Use this for data analysis, reporting, or when the user asks "
                "for a chart or visualization."
            ),
            parameters={
                "data": {
                    "type": "object",
                    "description": (
                        "Data to visualize. Can be a list of dicts, dict, or list of lists. "
                        "Example: [{'name': 'A', 'value': 10}, {'name': 'B', 'value': 20}]"
                    ),
                },
                "chart_type": {
                    "type": "string",
                    "enum": ["bar", "line", "pie", "scatter", "auto"],
                    "description": "Type of chart (auto-detected if not specified)",
                    "default": "auto",
                },
                "title": {
                    "type": "string",
                    "description": "Chart title",
                    "default": "Chart",
                },
                "x_key": {
                    "type": "string",
                    "description": "Key for x-axis (for dict data)",
                },
                "y_key": {
                    "type": "string",
                    "description": "Key for y-axis (for dict data)",
                },
                "save_path": {
                    "type": "string",
                    "description": "Optional path to save HTML file",
                },
            },
            required=["data"],
        )
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Generate a chart from the provided data."""
        data = kwargs.get("data")
        chart_type = kwargs.get("chart_type", "auto")
        title = kwargs.get("title", "Chart")
        x_key = kwargs.get("x_key")
        y_key = kwargs.get("y_key")
        save_path = kwargs.get("save_path")
        
        if not data:
            return {"output": "Error: No data provided for visualization."}
        
        try:
            # Step 1: Prepare data
            prepared = await ChartPrepare.prepare(
                data=data,
                chart_type=chart_type,
                x_key=x_key,
                y_key=y_key,
                title=title
            )
            
            if not prepared.get("success"):
                return {"output": f"Chart preparation failed: {prepared.get('error')}"}
            
            chart_config = prepared.get("chart_config", {})
            detected_type = prepared.get("chart_type", chart_type)
            
            # Step 2: Generate chart
            if save_path:
                # Ensure path is in workspace
                workspace = "/workspace/charts"
                os.makedirs(workspace, exist_ok=True)
                if not save_path.startswith("/"):
                    save_path = f"{workspace}/{save_path}"
                if not save_path.endswith(".html"):
                    save_path = f"{save_path}.html"
            else:
                # Generate default path
                chart_id = str(uuid.uuid4())[:8]
                workspace = "/workspace/charts"
                os.makedirs(workspace, exist_ok=True)
                save_path = f"{workspace}/chart_{chart_id}.html"
            
            result = await DataVisualization.visualize(
                chart_config=chart_config,
                output_format="html",
                save_path=save_path
            )
            
            if not result.get("success"):
                return {"output": f"Chart generation failed: {result.get('error')}"}
            
            return {
                "output": f"Chart generated successfully: {save_path}",
                "path": save_path,
                "chart_type": detected_type,
                "title": title,
            }
            
        except Exception as e:
            logger.exception("chart_viz_tool.failed", error=str(e))
            return {"output": f"Chart visualization failed: {str(e)}"}