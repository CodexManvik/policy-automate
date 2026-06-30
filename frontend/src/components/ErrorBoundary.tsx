/**
 * ErrorBoundary — catches render errors in any child subtree and prevents
 * a full white-screen unmount when decision_trace or other nullable fields
 * cause an unhandled exception during rendering.
 */

import { Component, type ReactNode, type ErrorInfo } from 'react';
import { AlertCircle, RefreshCw } from 'lucide-react';

interface Props {
  children: ReactNode;
  /** Optional label for the error panel header. */
  label?: string;
}

interface State {
  hasError: boolean;
  errorMessage: string;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, errorMessage: '' };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, errorMessage: error.message };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ErrorBoundary] Render error caught:', error, info.componentStack);
  }

  private handleReset = (): void => {
    this.setState({ hasError: false, errorMessage: '' });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="p-5 bg-red-950/20 border border-red-500/30 rounded-2xl flex flex-col gap-3">
          <div className="flex items-center gap-2 text-red-400">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span className="text-xs font-bold uppercase tracking-wider">
              {this.props.label ?? 'Render Error'}
            </span>
          </div>
          <p className="text-[11px] text-red-300 font-mono break-all">
            {this.state.errorMessage}
          </p>
          <button
            onClick={this.handleReset}
            className="self-start flex items-center gap-1.5 text-[11px] text-slate-400 hover:text-white transition-smooth"
          >
            <RefreshCw className="w-3 h-3" />
            Retry render
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
