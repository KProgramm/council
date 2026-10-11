import { Progress as ProgressPrimitive } from 'radix-ui'
import * as React from 'react'
import { cn } from '@/lib/utils'

function Progress({ className, value, color, ...props }: React.ComponentProps<typeof ProgressPrimitive.Root> & { color?: string }) {
  return (
    <ProgressPrimitive.Root className={cn('relative h-1.5 w-12 overflow-hidden rounded-full bg-muted', className)} value={value} {...props}>
      <ProgressPrimitive.Indicator className="h-full bg-primary transition-all" style={{ width: `${value ?? 0}%`, background: color }} />
    </ProgressPrimitive.Root>
  )
}

export { Progress }
