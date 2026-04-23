import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import '../styles/Header.css';
import logo from '../assests/image.png';
const NAV = [
  { label: 'Dashboard', to: '/dashboard' },
  { label: 'Master Data', to: '/master-data' },
  { label: 'Model Library', to: '/model-library' },
];

export default function Header() {
  const { pathname } = useLocation();

  return (
    <header className="header">
      <Link to="/" className="header-logo">
        <img src={logo} alt="Logo" className="header-logo-image" style={{ height: '40px' }} />
      </Link>

      <nav className="header-nav">
        {NAV.map(({ label, to }) => (
          <Link
            key={to}
            to={to}
            className={`nav-link${pathname === to || (to === '/dashboard' && pathname === '/dashboard') ? ' active' : ''}`}
          >
            {label}
          </Link>
        ))}
        <Link to="/new-app" className="nav-cta">
          + New App
        </Link>
      </nav>
    </header>
  );
}
