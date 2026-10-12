# backend/app/tools/chart_visualization/chart_prepare.py

import json
import structlog
from typing import Dict, Any, List, Optional

logger = structlog.get_logger("aether.chart_visualization.chart_prepare")


class ChartPrepare:
    """
    Prepare data for visualization.
    Converts raw data into chart-ready format.
    """
    
    @staticmethod
    async def prepare(
        data: Any,
        chart_type: str = "bar",
        x_key: Optional[str] = None,
        y_key: Optional[str] = None,
        title: str = "Chart"
    ) -> Dict[str, Any]:
        """
        Prepare data for chart generation.
        
        Args:
            data: Raw data (list of dicts, list of lists, or dict)
            chart_type: Type of chart (bar, line, pie, scatter)
            x_key: Key for x-axis (for dict data)
            y_key: Key for y-axis (for dict data)
            title: Chart title
            
        Returns:
            Chart-ready data structure
        """
        try:
            # Convert data to chart format
            chart_data = await ChartPrepare._convert_data(
                data, x_key, y_key
            )
            
            # Determine chart type if not specified
            if chart_type == "auto":
                chart_type = ChartPrepare._detect_chart_type(chart_data)
            
            # Build chart config
            config = {
                "type": chart_type,
                "data": chart_data,
                "title": title,
                "config": {
                    "responsive": True,
                    "maintainAspectRatio": False,
                }
            }
            
            logger.info("chart_prepare.complete", 
                       chart_type=chart_type,
                       data_points=len(chart_data.get("labels", [])))
            
            return {
                "success": True,
                "chart_config": config,
                "chart_type": chart_type,
            }
            
        except Exception as e:
            logger.exception("chart_prepare.failed", error=str(e))
            return {
                "success": False,
                "error": str(e),
            }
    
    @staticmethod
    async def _convert_data(
        data: Any,
        x_key: Optional[str] = None,
        y_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Convert various data formats to chart format."""
        # If data is a list of dicts
        if isinstance(data, list) and all(isinstance(d, dict) for d in data):
            if x_key and y_key:
                return {
                    "labels": [d.get(x_key, "") for d in data],
                    "datasets": [{
                        "label": "Values",
                        "data": [d.get(y_key, 0) for d in data],
                    }]
                }
            else:
                # Auto-detect keys
                all_keys = set()
                for d in data:
                    all_keys.update(d.keys())
                
                if len(all_keys) == 2:
                    keys = list(all_keys)
                    return {
                        "labels": [d.get(keys[0], "") for d in data],
                        "datasets": [{
                            "label": keys[1],
                            "data": [d.get(keys[1], 0) for d in data],
                        }]
                    }
                else:
                    return {
                        "labels": list(range(len(data))),
                        "datasets": [{
                            "label": "Values",
                            "data": data,
                        }]
                    }
        
        # If data is a dict
        elif isinstance(data, dict):
            if all(isinstance(v, (int, float)) for v in data.values()):
                return {
                    "labels": list(data.keys()),
                    "datasets": [{
                        "label": "Values",
                        "data": list(data.values()),
                    }]
                }
            else:
                raise ValueError("Dict data must have numeric values")
        
        # If data is a list of lists
        elif isinstance(data, list) and all(isinstance(d, list) for d in data):
            return {
                "labels": [str(d[0]) for d in data if d],
                "datasets": [{
                    "label": "Values",
                    "data": [d[1] for d in data if len(d) > 1],
                }]
            }
        
        # Fallback
        else:
            return {
                "labels": list(range(len(data))) if isinstance(data, list) else [],
                "datasets": [{
                    "label": "Values",
                    "data": data if isinstance(data, list) else [data],
                }]
            }
    
    @staticmethod
    def _detect_chart_type(data: Dict[str, Any]) -> str:
        """Auto-detect best chart type."""
        labels = data.get("labels", [])
        datasets = data.get("datasets", [])
        
        if not labels or not datasets:
            return "bar"
        
        # If few categories, use pie
        if len(labels) <= 8:
            return "pie"
        
        # If many data points, use line
        if len(labels) > 20:
            return "line"
        
        # Check if data is time-series
        # (simplified detection)
        first_label = str(labels[0]) if labels else ""
        if any(x in first_label for x in ["202", "Jan", "Mon", "Q"]):
            return "line"
        
        return "bar"