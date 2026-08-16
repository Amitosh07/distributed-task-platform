import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Badge } from './ui/Badge'
import { StatCard } from './ui/StatCard'
import { Button } from './ui/Button'

describe('UI Primitives', () => {
  it('renders a status badge with text and custom styling', () => {
    render(<Badge status="RUNNING" />)
    expect(screen.getByText('RUNNING')).toBeInTheDocument()
  })

  it('renders a StatCard with label and numeric value', () => {
    render(<StatCard label="Total Tasks" value={42} subValue="in project" />)
    expect(screen.getByText(/Total Tasks/i)).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.getByText('in project')).toBeInTheDocument()
  })

  it('renders a Button with loading spinner when isLoading is true', () => {
    render(<Button isLoading={true}>Submit Task</Button>)
    const btn = screen.getByRole('button')
    expect(btn).toBeDisabled()
    expect(screen.getByText('Submit Task')).toBeInTheDocument()
  })
})
