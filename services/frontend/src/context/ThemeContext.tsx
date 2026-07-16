import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

export type ThemeId = 'slate' | 'indigo' | 'forest' | 'sunset' | 'ocean' | 'rose' | 'night'

export interface Theme {
  id: ThemeId
  label: string
  primaryColor: string
  softColor: string
}

export const THEMES: Theme[] = [
  { id: 'slate', label: 'Slate', primaryColor: '#171717', softColor: '#e5e5e5' },
  { id: 'indigo', label: 'Indigo', primaryColor: '#4f46e5', softColor: '#e0e7ff' },
  { id: 'forest', label: 'Forest', primaryColor: '#16a34a', softColor: '#dcfce7' },
  { id: 'sunset', label: 'Sunset', primaryColor: '#ea580c', softColor: '#ffedd5' },
  { id: 'ocean', label: 'Ocean', primaryColor: '#0d9488', softColor: '#ccfbf1' },
  { id: 'rose', label: 'Rose', primaryColor: '#e11d48', softColor: '#ffe4e6' },
  { id: 'night', label: 'Night', primaryColor: '#818cf8', softColor: '#1e1b4b' },
]

const STORAGE_KEY = 'aria-theme'

interface ThemeContextValue {
  theme: ThemeId
  setTheme: (id: ThemeId) => void
  themes: Theme[]
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(() => {
    const stored = localStorage.getItem(STORAGE_KEY) as ThemeId | null
    return stored && THEMES.some((t) => t.id === stored) ? stored : 'slate'
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  return (
    <ThemeContext.Provider value={{ theme, setTheme: setThemeState, themes: THEMES }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}
