# backend/app/tools/chart_visualization/data_visualization.py

import json
import os
import uuid
import structlog
from datetime import datetime
from typing import Dict, Any, Optional

logger = structlog.get_logger("aether.chart_visualization.data_visualization")


class DataVisualization:
    """
    Generate charts and visualizations.
    Creates HTML with Chart.js or similar.
    """
    
    @staticmethod
    async def visualize(
        chart_config: Dict[str, Any],
        output_format: str = "html",
        save_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate a chart from config.
        
        Args:
            chart_config: Chart configuration from ChartPrepare
            output_format: 'html' or 'json'
            save_path: Optional path to save the chart
            
        Returns:
            Chart data or file path
        """
        try:
            chart_type = chart_config.get("type", "bar")
            data = chart_config.get("data", {})
            title = chart_config.get("title", "Chart")
            
            if output_format == "html":
                html = DataVisualization._generate_html(
                    chart_type, data, title
                )
                
                if save_path:
                    # Save HTML to file
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                    with open(save_path, "w") as f:
                        f.write(html)
                    
                    return {
                        "success": True,
                        "path": save_path,
                        "format": "html",
                        "chart_type": chart_type,
                    }
                else:
                    # Return HTML string
                    return {
                        "success": True,
                        "html": html,
                        "format": "html",
                        "chart_type": chart_type,
                    }
            
            elif output_format == "json":
                return {
                    "success": True,
                    "data": chart_config,
                    "format": "json",
                    "chart_type": chart_type,
                }
            
            else:
                return {
                    "success": False,
                    "error": f"Unsupported format: {output_format}"
                }
                
        except Exception as e:
            logger.exception("data_visualization.failed", error=str(e))
            return {
                "success": False,
                "error": str(e),
            }
    
    @staticmethod
    def _generate_html(chart_type: str, data: Dict[str, Any], title: str) -> str:
        """Generate HTML with Chart.js."""
        # Convert data to JSON
        data_json = json.dumps(data)
        
        # Chart.js configuration
        config = {
            "type": chart_type,
            "data": data,
            "options": {
                "responsive": True,
                "plugins": {
                    "title": {
                        "display": True,
                        "text": title,
                    }
                },
                "maintainAspectRatio": False,
            }
        }
        
        if chart_type == "pie":
            config["options"]["plugins"]["legend"] = {
                "position": "top",
            }
        
        config_json = json.dumps(config)
        
        html = f'''
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>{title}</title>
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                body {{ 
                    margin: 0; 
                    padding: 20px; 
                    background: #f5f5f5;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                }}
                .chart-container {{
                    max-width: 900px;
                    margin: 0 auto;
                    background: white;
                    padding: 20px;
                    border-radius: 12px;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                }}
                .chart-wrapper {{
                    position: relative;
                    height: 500px;
                }}
                h1 {{
                    text-align: center;
                    color: #333;
                    margin-top: 0;
                }}
                .info {{
                    text-align: center;
                    color: #666;
                    font-size: 14px;
                    margin-top: 15px;
                    border-top: 1px solid #eee;
                    padding-top: 15px;
                }}
            </style>
        </head>
        <body>
            <div class="chart-container">
                <h1>{title}</h1>
                <div class="chart-wrapper">
                    <canvas id="chart"></canvas>
                </div>
                <div class="info">
                    Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | Type: {chart_type}
                </div>
            </div>
            <script>
                const ctx = document.getElementById('chart').getContext('2d');
                new Chart(ctx, {config_json});
            </script>
        </body>
        </html>
        '''
        
        return html