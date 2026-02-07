"""
IMS 2.0 - JARVIS Visualization Engine
====================================

Data visualization and charting engine for JARVIS AI.
Generates charts, graphs, and visual insights from analytics data.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
from datetime import datetime, timedelta
import json


class ChartType(Enum):
    """Types of charts supported"""
    LINE = "line"
    BAR = "bar"
    PIE = "pie"
    DOUGHNUT = "doughnut"
    AREA = "area"
    SCATTER = "scatter"
    BUBBLE = "bubble"
    GAUGE = "gauge"
    HEATMAP = "heatmap"
    TABLE = "table"
    HISTOGRAM = "histogram"
    BOX_PLOT = "box_plot"
    WATERFALL = "waterfall"
    FUNNEL = "funnel"
    TREEMAP = "treemap"


class ChartTheme(Enum):
    """Chart themes"""
    LIGHT = "light"
    DARK = "dark"
    PROFESSIONAL = "professional"
    VIBRANT = "vibrant"
    MINIMAL = "minimal"


@dataclass
class ChartDataPoint:
    """Individual data point in chart"""
    label: str
    value: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChartSeries:
    """Data series for chart"""
    name: str
    data: List[float]
    color: Optional[str] = None
    style: str = "solid"  # solid, dashed, dotted


@dataclass
class ChartAxis:
    """Chart axis configuration"""
    title: str
    type: str = "linear"  # linear, logarithmic, category, time
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    tick_format: Optional[str] = None
    labels: List[str] = field(default_factory=list)


@dataclass
class Chart:
    """Chart definition"""
    chart_id: str
    title: str
    subtitle: Optional[str]
    chart_type: ChartType
    series: List[ChartSeries]
    x_axis: Optional[ChartAxis]
    y_axis: Optional[ChartAxis]
    legend_enabled: bool = True
    theme: ChartTheme = ChartTheme.PROFESSIONAL
    width: int = 800
    height: int = 400
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Dashboard:
    """Dashboard containing multiple charts"""
    dashboard_id: str
    title: str
    description: str
    charts: List[Chart]
    layout: str = "grid"  # grid, flex, custom
    refresh_interval: int = 60  # seconds
    created_at: datetime = field(default_factory=datetime.now)
    created_by: str = "JARVIS"


class ColorPalette(Enum):
    """Color palettes for charts"""
    DEFAULT = [
        "#4285F4", "#EA4335", "#FBBC04", "#34A853",
        "#FF6D00", "#AB47BC", "#00BCD4", "#FF5252"
    ]
    PROFESSIONAL = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
        "#9467bd", "#8c564b", "#e377c2", "#7f7f7f"
    ]
    VIBRANT = [
        "#FF0066", "#00CC99", "#0066FF", "#FFCC00",
        "#FF6600", "#99FF00", "#FF0099", "#00FFFF"
    ]
    MINIMAL = [
        "#333333", "#666666", "#999999", "#CCCCCC",
        "#FF9999", "#99CCFF", "#99FF99", "#FFCC99"
    ]


class JarvisVisualizationEngine:
    """Visualization engine for JARVIS data"""

    def __init__(self):
        self.charts: Dict[str, Chart] = {}
        self.dashboards: Dict[str, Dashboard] = {}
        self.chart_history: List[Chart] = []
        self.color_palette = ColorPalette.PROFESSIONAL.value

    def create_line_chart(
        self,
        title: str,
        data: List[Tuple[str, float]],
        subtitle: Optional[str] = None,
        x_label: str = "Time",
        y_label: str = "Value"
    ) -> Chart:
        """Create line chart for time series data"""

        labels, values = zip(*data) if data else ([], [])

        chart = Chart(
            chart_id=f"chart_{int(datetime.now().timestamp())}",
            title=title,
            subtitle=subtitle,
            chart_type=ChartType.LINE,
            series=[
                ChartSeries(
                    name=title,
                    data=list(values),
                    color=self.color_palette[0]
                )
            ],
            x_axis=ChartAxis(
                title=x_label,
                type="category",
                labels=list(labels)
            ),
            y_axis=ChartAxis(
                title=y_label,
                type="linear"
            )
        )

        self.charts[chart.chart_id] = chart
        self.chart_history.append(chart)

        return chart

    def create_bar_chart(
        self,
        title: str,
        categories: List[str],
        values: List[float],
        subtitle: Optional[str] = None,
        is_stacked: bool = False
    ) -> Chart:
        """Create bar chart for categorical comparison"""

        chart = Chart(
            chart_id=f"chart_{int(datetime.now().timestamp())}",
            title=title,
            subtitle=subtitle,
            chart_type=ChartType.BAR,
            series=[
                ChartSeries(
                    name=title,
                    data=values,
                    color=self.color_palette[0]
                )
            ],
            x_axis=ChartAxis(
                title="Category",
                type="category",
                labels=categories
            ),
            y_axis=ChartAxis(
                title="Value",
                type="linear"
            ),
            metadata={"stacked": is_stacked}
        )

        self.charts[chart.chart_id] = chart
        self.chart_history.append(chart)

        return chart

    def create_pie_chart(
        self,
        title: str,
        labels: List[str],
        values: List[float],
        subtitle: Optional[str] = None,
        is_doughnut: bool = False
    ) -> Chart:
        """Create pie or doughnut chart"""

        chart = Chart(
            chart_id=f"chart_{int(datetime.now().timestamp())}",
            title=title,
            subtitle=subtitle,
            chart_type=ChartType.DOUGHNUT if is_doughnut else ChartType.PIE,
            series=[
                ChartSeries(
                    name=label,
                    data=[value],
                    color=self.color_palette[i % len(self.color_palette)]
                )
                for i, (label, value) in enumerate(zip(labels, values))
            ],
            metadata={
                "labels": labels,
                "values": values
            }
        )

        self.charts[chart.chart_id] = chart
        self.chart_history.append(chart)

        return chart

    def create_area_chart(
        self,
        title: str,
        data_series: List[Tuple[str, List[float]]],
        time_labels: List[str],
        subtitle: Optional[str] = None
    ) -> Chart:
        """Create area chart for stacked data"""

        series = [
            ChartSeries(
                name=name,
                data=values,
                color=self.color_palette[i % len(self.color_palette)]
            )
            for i, (name, values) in enumerate(data_series)
        ]

        chart = Chart(
            chart_id=f"chart_{int(datetime.now().timestamp())}",
            title=title,
            subtitle=subtitle,
            chart_type=ChartType.AREA,
            series=series,
            x_axis=ChartAxis(
                title="Time",
                type="category",
                labels=time_labels
            ),
            y_axis=ChartAxis(
                title="Value",
                type="linear"
            )
        )

        self.charts[chart.chart_id] = chart
        self.chart_history.append(chart)

        return chart

    def create_gauge_chart(
        self,
        title: str,
        value: float,
        min_value: float = 0,
        max_value: float = 100,
        thresholds: Optional[List[float]] = None,
        subtitle: Optional[str] = None
    ) -> Chart:
        """Create gauge chart for KPI visualization"""

        if thresholds is None:
            thresholds = [33, 66, 100]

        chart = Chart(
            chart_id=f"chart_{int(datetime.now().timestamp())}",
            title=title,
            subtitle=subtitle,
            chart_type=ChartType.GAUGE,
            series=[
                ChartSeries(
                    name=title,
                    data=[value]
                )
            ],
            metadata={
                "min": min_value,
                "max": max_value,
                "thresholds": thresholds,
                "value": value,
                "percentage": (value / max_value) * 100
            }
        )

        self.charts[chart.chart_id] = chart
        self.chart_history.append(chart)

        return chart

    def create_comparison_chart(
        self,
        title: str,
        categories: List[str],
        series1_name: str,
        series1_data: List[float],
        series2_name: str,
        series2_data: List[float],
        subtitle: Optional[str] = None
    ) -> Chart:
        """Create comparison chart with multiple series"""

        chart = Chart(
            chart_id=f"chart_{int(datetime.now().timestamp())}",
            title=title,
            subtitle=subtitle,
            chart_type=ChartType.BAR,
            series=[
                ChartSeries(
                    name=series1_name,
                    data=series1_data,
                    color=self.color_palette[0]
                ),
                ChartSeries(
                    name=series2_name,
                    data=series2_data,
                    color=self.color_palette[1]
                )
            ],
            x_axis=ChartAxis(
                title="Category",
                type="category",
                labels=categories
            ),
            y_axis=ChartAxis(
                title="Value",
                type="linear"
            )
        )

        self.charts[chart.chart_id] = chart
        self.chart_history.append(chart)

        return chart

    def create_heatmap_chart(
        self,
        title: str,
        data: List[List[float]],
        x_labels: List[str],
        y_labels: List[str],
        subtitle: Optional[str] = None
    ) -> Chart:
        """Create heatmap for 2D data visualization"""

        chart = Chart(
            chart_id=f"chart_{int(datetime.now().timestamp())}",
            title=title,
            subtitle=subtitle,
            chart_type=ChartType.HEATMAP,
            series=[],
            x_axis=ChartAxis(
                title="",
                type="category",
                labels=x_labels
            ),
            y_axis=ChartAxis(
                title="",
                type="category",
                labels=y_labels
            ),
            metadata={
                "data": data,
                "min_value": min(min(row) for row in data),
                "max_value": max(max(row) for row in data)
            }
        )

        self.charts[chart.chart_id] = chart
        self.chart_history.append(chart)

        return chart

    def create_table_visualization(
        self,
        title: str,
        headers: List[str],
        rows: List[List[Any]],
        subtitle: Optional[str] = None,
        sortable: bool = True,
        searchable: bool = True
    ) -> Chart:
        """Create table for structured data display"""

        chart = Chart(
            chart_id=f"chart_{int(datetime.now().timestamp())}",
            title=title,
            subtitle=subtitle,
            chart_type=ChartType.TABLE,
            series=[],
            metadata={
                "headers": headers,
                "rows": rows,
                "sortable": sortable,
                "searchable": searchable,
                "row_count": len(rows),
                "column_count": len(headers)
            }
        )

        self.charts[chart.chart_id] = chart
        self.chart_history.append(chart)

        return chart

    def create_sales_dashboard(
        self,
        metrics: Dict[str, Any]
    ) -> Dashboard:
        """Create sales analytics dashboard"""

        charts = []

        # Sales trend line chart
        sales_data = metrics.get("daily_sales", [])
        if sales_data:
            trend_chart = self.create_line_chart(
                title="Daily Sales Trend",
                data=[(f"Day {i+1}", v) for i, v in enumerate(sales_data)],
                y_label="Sales (₹)"
            )
            charts.append(trend_chart)

        # Sales by product bar chart
        products = metrics.get("products", [])
        if products:
            product_names = [p.get("name", f"Product {i}") for i, p in enumerate(products)]
            product_sales = [p.get("sales", 0) for p in products]
            product_chart = self.create_bar_chart(
                title="Sales by Product",
                categories=product_names,
                values=product_sales
            )
            charts.append(product_chart)

        # Sales by store pie chart
        stores = metrics.get("stores", [])
        if stores:
            store_names = [s.get("name", f"Store {i}") for i, s in enumerate(stores)]
            store_sales = [s.get("sales", 0) for s in stores]
            store_chart = self.create_pie_chart(
                title="Sales Distribution",
                labels=store_names,
                values=store_sales
            )
            charts.append(store_chart)

        # Revenue KPI gauge
        total_revenue = metrics.get("total_revenue", 0)
        target_revenue = metrics.get("target_revenue", 1000000)
        gauge_chart = self.create_gauge_chart(
            title="Revenue Target Progress",
            value=total_revenue,
            max_value=target_revenue
        )
        charts.append(gauge_chart)

        dashboard = Dashboard(
            dashboard_id=f"dashboard_{int(datetime.now().timestamp())}",
            title="Sales Analytics Dashboard",
            description="Real-time sales performance metrics",
            charts=charts
        )

        self.dashboards[dashboard.dashboard_id] = dashboard

        return dashboard

    def create_inventory_dashboard(
        self,
        metrics: Dict[str, Any]
    ) -> Dashboard:
        """Create inventory management dashboard"""

        charts = []

        # Stock level trend
        stock_data = metrics.get("stock_levels", [])
        if stock_data:
            stock_chart = self.create_line_chart(
                title="Inventory Level Trend",
                data=[(f"Day {i+1}", v) for i, v in enumerate(stock_data)],
                y_label="Units"
            )
            charts.append(stock_chart)

        # Stock status distribution
        critical_items = metrics.get("critical_items", 0)
        low_items = metrics.get("low_items", 0)
        optimal_items = metrics.get("optimal_items", 0)
        overstock_items = metrics.get("overstock_items", 0)

        status_chart = self.create_pie_chart(
            title="Stock Status Distribution",
            labels=["Critical", "Low", "Optimal", "Overstock"],
            values=[critical_items, low_items, optimal_items, overstock_items],
            is_doughnut=True
        )
        charts.append(status_chart)

        # Turnover rate by category
        categories = metrics.get("categories", [])
        if categories:
            category_names = [c.get("name", f"Category {i}") for i, c in enumerate(categories)]
            turnover_rates = [c.get("turnover_rate", 0) for c in categories]
            turnover_chart = self.create_bar_chart(
                title="Inventory Turnover by Category",
                categories=category_names,
                values=turnover_rates
            )
            charts.append(turnover_chart)

        dashboard = Dashboard(
            dashboard_id=f"dashboard_{int(datetime.now().timestamp())}",
            title="Inventory Management Dashboard",
            description="Stock levels and inventory metrics",
            charts=charts
        )

        self.dashboards[dashboard.dashboard_id] = dashboard

        return dashboard

    def create_compliance_dashboard(
        self,
        metrics: Dict[str, Any]
    ) -> Dashboard:
        """Create compliance monitoring dashboard"""

        charts = []

        # Compliance score gauge
        compliance_score = metrics.get("compliance_score", 85)
        score_chart = self.create_gauge_chart(
            title="Overall Compliance Score",
            value=compliance_score,
            max_value=100,
            thresholds=[30, 70, 100]
        )
        charts.append(score_chart)

        # Violations by severity
        violations = metrics.get("violations_by_severity", {})
        if violations:
            violation_labels = list(violations.keys())
            violation_counts = list(violations.values())
            violation_chart = self.create_bar_chart(
                title="Violations by Severity",
                categories=violation_labels,
                values=violation_counts
            )
            charts.append(violation_chart)

        # Violations by category
        by_category = metrics.get("violations_by_category", {})
        if by_category:
            category_labels = list(by_category.keys())
            category_counts = list(by_category.values())
            category_chart = self.create_pie_chart(
                title="Violations by Category",
                labels=category_labels,
                values=category_counts
            )
            charts.append(category_chart)

        dashboard = Dashboard(
            dashboard_id=f"dashboard_{int(datetime.now().timestamp())}",
            title="Compliance Monitoring Dashboard",
            description="Compliance status and violations",
            charts=charts
        )

        self.dashboards[dashboard.dashboard_id] = dashboard

        return dashboard

    def export_chart_json(self, chart: Chart) -> str:
        """Export chart as JSON for frontend rendering"""

        return json.dumps({
            "chart_id": chart.chart_id,
            "title": chart.title,
            "subtitle": chart.subtitle,
            "type": chart.chart_type.value,
            "theme": chart.theme.value,
            "series": [
                {
                    "name": s.name,
                    "data": s.data,
                    "color": s.color,
                    "style": s.style
                }
                for s in chart.series
            ],
            "x_axis": {
                "title": chart.x_axis.title if chart.x_axis else "",
                "type": chart.x_axis.type if chart.x_axis else "category",
                "labels": chart.x_axis.labels if chart.x_axis else []
            } if chart.x_axis else None,
            "y_axis": {
                "title": chart.y_axis.title if chart.y_axis else "",
                "type": chart.y_axis.type if chart.y_axis else "linear",
                "min": chart.y_axis.min_value if chart.y_axis else None,
                "max": chart.y_axis.max_value if chart.y_axis else None
            } if chart.y_axis else None,
            "legend_enabled": chart.legend_enabled,
            "width": chart.width,
            "height": chart.height,
            "metadata": chart.metadata
        })

    def export_dashboard_json(self, dashboard: Dashboard) -> str:
        """Export dashboard as JSON"""

        return json.dumps({
            "dashboard_id": dashboard.dashboard_id,
            "title": dashboard.title,
            "description": dashboard.description,
            "layout": dashboard.layout,
            "refresh_interval": dashboard.refresh_interval,
            "charts": json.loads("[" + ", ".join(
                self.export_chart_json(chart)
                for chart in dashboard.charts
            ) + "]"),
            "created_at": dashboard.created_at.isoformat(),
            "created_by": dashboard.created_by
        })

    def get_chart_by_id(self, chart_id: str) -> Optional[Chart]:
        """Retrieve chart by ID"""
        return self.charts.get(chart_id)

    def get_dashboard_by_id(self, dashboard_id: str) -> Optional[Dashboard]:
        """Retrieve dashboard by ID"""
        return self.dashboards.get(dashboard_id)

    def list_recent_charts(self, limit: int = 10) -> List[Chart]:
        """Get recently created charts"""
        return self.chart_history[-limit:]

    def list_dashboards(self) -> List[Dashboard]:
        """List all dashboards"""
        return list(self.dashboards.values())


# Initialize global visualization engine
jarvis_visualizer = JarvisVisualizationEngine()
