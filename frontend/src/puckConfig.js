import React from 'react';

export const puckConfig = {
  root: {
    render: ({ children }) => (
      <div style={{ background: "#FFFFFF", minHeight: "100%", color: "#1a1a1a" }}>
        {children}
      </div>
    ),
  },
  components: {
    AppHeader: {
      fields: {
        title: { type: "text" },
        show_back: { type: "radio", options: [{ label: "Yes", value: true }, { label: "No", value: false }] }
      },
      defaultProps: { title: "AI Vision", show_back: true },
      render: ({ title, show_back }) => (
        <div style={{ width: '100%', background: '#dc143c', padding: '12px 16px', borderBottom: '1px solid rgba(0,0,0,0.1)', display: 'flex', alignItems: 'center', gap: 12 }}>
          {show_back && <span style={{ fontSize: 14, color: '#fff' }}>←</span>}
          <span style={{ fontWeight: 700, fontSize: 15, color: '#fff' }}>{title}</span>
        </div>
      )
    },
    CameraView: {
      fields: {
        camera: { type: "select", options: [{ label: "Back", value: "back" }, { label: "Front", value: "front" }] },
        resolution: { type: "select", options: [{ label: "High", value: "high" }, { label: "Medium", value: "medium" }, { label: "Low", value: "low" }] }
      },
      defaultProps: { camera: "back", resolution: "medium" },
      render: () => (
        <div style={{ width: '100%', height: 200, background: '#F8F9FA', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 10, border: '1px dashed #dc143c' }}>
          <span style={{ fontSize: 32 }}>📷</span>
          <span style={{ fontSize: 11, color: '#666' }}>Live Camera Stream</span>
        </div>
      )
    },
    DetectionOverlay: {
      fields: {
        box_color: { type: "text" },
        line_thickness: { type: "number" }
      },
      defaultProps: { box_color: "#dc143c", line_thickness: 2 },
      render: ({ box_color, line_thickness }) => (
        <div style={{ width: '100%', height: 120, position: 'relative', border: '1px dashed rgba(220, 20, 60, 0.3)', background: 'rgba(220, 20, 60, 0.05)' }}>
           <div style={{ position: 'absolute', top: 20, left: 40, width: 60, height: 60, border: `${line_thickness}px solid ${box_color}`, borderRadius: 4 }}>
              <div style={{ position: 'absolute', top: -18, left: -2, background: box_color, color: '#fff', fontSize: 8, padding: '2px 4px', borderRadius: 2 }}>Object 92%</div>
           </div>
        </div>
      )
    },
    InfoCard: {
      fields: {
        title: { type: "text" },
        subtitle: { type: "text" }
      },
      defaultProps: { title: "Status", subtitle: "System Normal" },
      render: ({ title, subtitle }) => (
        <div style={{ width: '100%', background: '#FFFFFF', borderRadius: 12, padding: 16, border: '1px solid #eeeeee', boxShadow: '0 2px 4px rgba(0,0,0,0.05)' }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#dc143c', marginBottom: 4 }}>{title}</div>
          <div style={{ fontSize: 12, color: '#666' }}>{subtitle}</div>
        </div>
      )
    },
    ActionGrid: {
      fields: {
        columns: { type: "number" }
      },
      defaultProps: { columns: 2 },
      render: ({ columns }) => (
        <div style={{ width: '100%', display: 'grid', gridTemplateColumns: `repeat(${columns || 2}, 1fr)`, gap: 8 }}>
          {[1, 2, 3, 4].map(i => (
            <div key={i} style={{ aspectRatio: '1', background: '#FFFFFF', borderRadius: 8, border: '1px solid #eeeeee', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, cursor: 'pointer', transition: 'all 0.2s' }}>🔘</div>
          ))}
        </div>
      )
    },
    ResultList: {
      fields: {
        max_results: { type: "number" },
        show_bars: { type: "radio", options: [{ label: "Yes", value: true }, { label: "No", value: false }] }
      },
      defaultProps: { max_results: 5, show_bars: true },
      render: () => (
        <div style={{ width: '100%', background: '#FFFFFF', borderRadius: 12, overflow: 'hidden', border: '1px solid #eeeeee' }}>
          {[1, 2, 3].map(i => (
            <div key={i} style={{ padding: '10px 12px', borderBottom: '1px solid #eeeeee', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 12, color: '#1a1a1a' }}>Detected Object #{i}</span>
              <span style={{ fontSize: 11, color: '#28a745', fontWeight: 700 }}>98%</span>
            </div>
          ))}
        </div>
      )
    }
  }
};
