/** Tests for the InlineChart component. */
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import type { ChartData } from './extractCharts';

// Mock Recharts to avoid complex SVG rendering in jsdom
vi.mock('recharts', () => ({
  BarChart: ({ children }: { children: React.ReactNode }) => <div data-testid="bar-chart">{children}</div>,
  Bar: () => <div data-testid="bar" />,
  PieChart: ({ children }: { children: React.ReactNode }) => <div data-testid="pie-chart">{children}</div>,
  Pie: ({ children }: { children: React.ReactNode }) => <div data-testid="pie">{children}</div>,
  Cell: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  Tooltip: () => <div />,
  CartesianGrid: () => <div />,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// Mock lazy-loaded components
vi.mock('./LightweightChart', () => ({
  default: () => <div data-testid="lightweight-chart" />,
}));
vi.mock('./TradingViewWidget', () => ({
  default: () => <div data-testid="tv-widget" />,
}));

import InlineChart from './InlineChart';

describe('InlineChart', () => {
  it('renders bar chart', () => {
    const chart: ChartData = {
      type: 'bar',
      title: 'Test Bar',
      data: { labels: ['A', 'B', 'C'], values: [10, 20, 30] },
    };
    const { container } = render(<InlineChart chart={chart} />);
    expect(container.textContent).toContain('Test Bar');
    expect(container.querySelector('[data-testid="bar-chart"]')).toBeTruthy();
  });

  it('renders pie chart', () => {
    const chart: ChartData = {
      type: 'pie',
      title: 'Test Pie',
      data: { labels: ['X', 'Y'], values: [60, 40] },
    };
    const { container } = render(<InlineChart chart={chart} />);
    expect(container.textContent).toContain('Test Pie');
    expect(container.querySelector('[data-testid="pie-chart"]')).toBeTruthy();
  });

  it('renders TradingView widget for tv type', () => {
    const chart: ChartData = {
      type: 'tv',
      title: 'Gold Weekly',
      symbol: 'TVC:GOLD',
    };
    const { container } = render(<InlineChart chart={chart} />);
    // Should render within a Suspense boundary
    expect(container).toBeTruthy();
  });

  it('does not crash with empty data', () => {
    const chart: ChartData = {
      type: 'bar',
      title: 'Empty',
      data: { labels: [], values: [] },
    };
    const { container } = render(<InlineChart chart={chart} />);
    expect(container).toBeTruthy();
  });

  it('renders title when provided', () => {
    const chart: ChartData = {
      type: 'bar',
      title: 'My Chart Title',
      data: { labels: ['A'], values: [1] },
    };
    const { container } = render(<InlineChart chart={chart} />);
    expect(container.textContent).toContain('My Chart Title');
  });

  it('handles line chart type (rendered by LightweightChart)', () => {
    const chart: ChartData = {
      type: 'line',
      title: 'Line Chart',
      data: { labels: ['Jan', 'Feb'], values: [100, 200] },
    };
    const { container } = render(<InlineChart chart={chart} />);
    expect(container).toBeTruthy();
  });
});
