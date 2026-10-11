import * as React from 'react'
import { cn } from '@/lib/utils'

function Alert({ className, ...props }: React.ComponentProps<'div'>) {
  return <div role="alert" className={cn('relative flex w-full items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm', className)} {...props} />
}

function AlertTitle({ className, ...props }: React.ComponentProps<'div'>) {
  return <div className={cn('font-medium text-destructive', className)} {...props} />
}

function AlertDescription({ className, ...props }: React.ComponentProps<'div'>) {
  return <div className={cn('text-muted-foreground', className)} {...props} />
}

export { Alert, AlertTitle, AlertDescription }
