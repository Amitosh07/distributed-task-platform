import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Badge } from './ui'
describe('status badge', () => { it('renders a task status', () => { render(<Badge status="RUNNING"/>); expect(screen.getByText('RUNNING')).toBeInTheDocument() }) })
