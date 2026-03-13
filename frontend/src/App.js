import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Header from './components/Header';
import LandingPage from './components/LandingPage';
import Dashboard from './components/Dashboard';
import NewApp from './components/NewApp';
import AppBuilder from './components/AppBuilder';
import ModelLibrary from './components/ModelsBrowser';
import MasterData from './components/MasterData';

export default function App() {
  return (
    <BrowserRouter>
      <div className="app-wrapper">
        <Header />
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/model-library" element={<ModelLibrary />} />
          <Route path="/master-data" element={<MasterData />} />
          <Route path="/new-app" element={<NewApp />} />
          <Route path="/apps/:id" element={<AppBuilder />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}
