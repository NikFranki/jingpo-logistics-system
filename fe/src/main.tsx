import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import 'antd/dist/reset.css'
import App from './App'
import './styles.css'
import { antdTheme } from './theme'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider theme={antdTheme}>
      <App />
    </ConfigProvider>
  </React.StrictMode>,
)
