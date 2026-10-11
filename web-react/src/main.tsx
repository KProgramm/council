import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './council.css'
import CouncilApp from './CouncilApp.tsx'

const stored = localStorage.getItem('council-theme')
const dark = stored ? stored === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches
document.documentElement.classList.toggle('dark', dark)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <CouncilApp />
  </StrictMode>,
)
