import * as echarts from "echarts/core";
import { BarChart, LineChart, MapChart, ScatterChart } from "echarts/charts";
import {
  AxisPointerComponent,
  DataZoomInsideComponent,
  GeoComponent,
  GridComponent,
  MarkAreaComponent,
  TimelineComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { LabelLayout } from "echarts/features";
import { CanvasRenderer, SVGRenderer } from "echarts/renderers";

echarts.use([
  ScatterChart,
  LineChart,
  BarChart,
  MapChart,
  TitleComponent,
  TooltipComponent,
  GridComponent,
  VisualMapComponent,
  TimelineComponent,
  MarkAreaComponent,
  GeoComponent,
  AxisPointerComponent,
  DataZoomInsideComponent,
  LabelLayout,
  CanvasRenderer,
  SVGRenderer,
]);

window.echarts = echarts;
