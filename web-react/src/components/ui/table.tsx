import * as React from 'react'
import { cn } from '@/lib/utils'

const Table = ({ className, ...props }: React.ComponentProps<'table'>) => <table className={cn('w-full table-fixed caption-bottom text-sm', className)} {...props} />
const TableHeader = (props: React.ComponentProps<'thead'>) => <thead {...props} />
const TableBody = (props: React.ComponentProps<'tbody'>) => <tbody {...props} />
const TableRow = ({ className, ...props }: React.ComponentProps<'tr'>) => <tr className={cn('border-b last:border-0', className)} {...props} />
const TableHead = ({ className, ...props }: React.ComponentProps<'th'>) => <th className={cn('h-8 px-1 text-center font-mono text-[10px] font-medium text-muted-foreground first:text-left', className)} {...props} />
const TableCell = ({ className, ...props }: React.ComponentProps<'td'>) => <td className={cn('px-1 py-2.5 text-center first:text-left', className)} {...props} />

export { Table, TableHeader, TableBody, TableRow, TableHead, TableCell }
