import React from 'react';
import { Link } from 'react-router-dom';
import { Zap, Cpu, Layers, ArrowRight, ScanLine, PackageCheck, Database } from 'lucide-react';
import '../styles/LandingPage.css';

const FEATURES = [
  {
    icon: <ScanLine size={20} />,
    title: 'YOLO Model Upload',
    desc: 'Drop any .pt file — classes are auto-detected. Zero manual configuration.',
  },
  {
    icon: <Cpu size={20} />,
    title: 'Auto TFLite Conversion',
    desc: 'PyTorch models convert to TFLite in the background, ready for Android.',
  },
  {
    icon: <Layers size={20} />,
    title: 'Sequential Inspection Flows',
    desc: 'Chain multiple models into ordered steps — VIN scan, component check, QC pass.',
  },
  {
    icon: <PackageCheck size={20} />,
    title: 'One-Click APK Build',
    desc: 'Generate a signed Android APK from your configured workflow in seconds.',
  },
  {
    icon: <Zap size={20} />,
    title: 'Live Build Console',
    desc: 'Watch real-time Gradle logs as your app compiles. Download when ready.',
  },
  {
    icon: <Database size={20} />,
    title: 'Master Data Mapping',
    desc: 'Define platform-to-model code mappings globally and reuse across all apps.',
  },
];

export default function LandingPage() {
  return (
    <div className="landing-root">

      {/* ── Hero ──────────────────────────────────────────────── */}
      <section className="landing-hero">
        <div className="landing-hero-content">
          <div className="landing-eyebrow">
            <span className="landing-eyebrow-dot" />
            Vehicle Inspection AI Studio
          </div>

          <h1 className="landing-title">
            Turn YOLO Models
            <span className="landing-title-line2 gradient-text">Into Inspection Apps</span>
          </h1>

          <p className="landing-subtitle">
            Upload your trained detection models, configure sequential inspection workflows, and ship a production Android APK — without writing a single line of mobile code.
          </p>

          <div className="landing-cta-row">
            <Link to="/dashboard" className="landing-cta-primary">
              Open Dashboard
              <ArrowRight size={16} />
            </Link>
            <Link to="/new-app" className="landing-cta-secondary">
              Create Your First App
            </Link>
          </div>
        </div>
      </section>

      {/* ── Stats bar ─────────────────────────────────────────── */}
      <div className="landing-stats-bar">
        {[
          { value: 'YOLO', label: 'Model Format' },
          { value: 'TFLite', label: 'Converted To' },
          { value: 'APK', label: 'Output Format' },
          { value: 'Android', label: 'Target Platform' },
        ].map(s => (
          <div key={s.label} className="landing-stat">
            <span className="landing-stat-value gradient-text">{s.value}</span>
            <span className="landing-stat-label">{s.label}</span>
          </div>
        ))}
      </div>

      {/* ── Features ──────────────────────────────────────────── */}
      <section className="landing-features">
        <div className="landing-features-label">Everything you need</div>
        <div className="landing-features-grid">
          {FEATURES.map((f, i) => (
            <div
              key={f.title}
              className="landing-feature-card"
              style={{ animationDelay: `${0.35 + i * 0.07}s` }}
            >
              <div className="landing-feature-icon">{f.icon}</div>
              <div className="landing-feature-title">{f.title}</div>
              <div className="landing-feature-desc">{f.desc}</div>
            </div>
          ))}
        </div>
      </section>

    </div>
  );
}
