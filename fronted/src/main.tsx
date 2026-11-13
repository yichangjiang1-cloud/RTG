import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { installClientLogCapture } from './utils/logger'
import './index.css'

installClientLogCapture()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
