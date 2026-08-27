/** Tests for ChatHeader component and collapsed explorer reopen control. */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChatHeader } from './ChatHeader';
import { useModelEffort } from './useModelEffort';

// Mock the useModelEffort hook
vi.mock('./useModelEffort', () => ({
  useModelEffort: vi.fn(),
}));

const mockOnReset = vi.fn();
const mockOnAbort = vi.fn();
const mockOnToggleFileBrowser = vi.fn();
const mockOnToggleMobileTopBar = vi.fn();

const defaultMockHook = {
  modelOptions: [
    { value: 'gpt-4', label: 'GPT-4' },
    { value: 'gpt-3.5', label: 'GPT-3.5' },
  ],
  effortOptions: [
    { value: 'fast', label: 'Fast' },
    { value: 'balanced', label: 'Balanced' },
  ],
  selectedModel: 'gpt-4',
  selectedEffort: 'balanced',
  selectedEffortLabel: 'Balanced',
  handleModelChange: vi.fn(),
  handleEffortChange: vi.fn(),
  controlsDisabled: false,
  uiError: null,
};

describe('ChatHeader', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders COMMS header with model selectors', () => {
    const mockUseModelEffort = vi.mocked(useModelEffort);
    mockUseModelEffort.mockReturnValue(defaultMockHook);

    render(
      <ChatHeader
        onReset={mockOnReset}
        onAbort={mockOnAbort}
        isGenerating={false}
      />
    );

    expect(screen.getByText('Comms')).toBeInTheDocument();
    expect(screen.getByText('GPT-4')).toBeInTheDocument();
    expect(screen.getByText('Balanced')).toBeInTheDocument();
  });

  it('shows the file browser expand button when provided', () => {
    const mockUseModelEffort = vi.mocked(useModelEffort);
    mockUseModelEffort.mockReturnValue(defaultMockHook);

    render(
      <ChatHeader
        onReset={mockOnReset}
        onAbort={mockOnAbort}
        isGenerating={false}
        onToggleFileBrowser={mockOnToggleFileBrowser}
      />
    );

    const expandButton = screen.getByRole('button', { name: /open file explorer/i });
    expect(expandButton).toBeInTheDocument();
    expect(expandButton).toHaveAttribute('aria-label', 'Open file explorer');
    expect(expandButton).toHaveAttribute('title', 'Open file explorer (Ctrl+B)');
  });

  it('shows the stacked mobile chrome control when top bar toggle is provided', () => {
    const mockUseModelEffort = vi.mocked(useModelEffort);
    mockUseModelEffort.mockReturnValue(defaultMockHook);

    render(
      <ChatHeader
        onReset={mockOnReset}
        onAbort={mockOnAbort}
        isGenerating={false}
        onToggleFileBrowser={mockOnToggleFileBrowser}
        isFileBrowserCollapsed={true}
        onToggleMobileTopBar={mockOnToggleMobileTopBar}
        isMobileTopBarHidden={false}
      />
    );

    expect(screen.getByRole('button', { name: /hide header controls/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /open file explorer/i })).toBeInTheDocument();
  });

  it('does not show the expand button when not provided', () => {
    const mockUseModelEffort = vi.mocked(useModelEffort);
    mockUseModelEffort.mockReturnValue(defaultMockHook);

    render(
      <ChatHeader
        onReset={mockOnReset}
        onAbort={mockOnAbort}
        isGenerating={false}
      />
    );

    expect(screen.queryByRole('button', { name: /open file explorer/i })).not.toBeInTheDocument();
  });

  it('calls onToggleFileBrowser when expand button is clicked', () => {
    const mockUseModelEffort = vi.mocked(useModelEffort);
    mockUseModelEffort.mockReturnValue(defaultMockHook);

    render(
      <ChatHeader
        onReset={mockOnReset}
        onAbort={mockOnAbort}
        isGenerating={false}
        onToggleFileBrowser={mockOnToggleFileBrowser}
      />
    );

    const expandButton = screen.getByRole('button', { name: /open file explorer/i });
    fireEvent.click(expandButton);

    expect(mockOnToggleFileBrowser).toHaveBeenCalledTimes(1);
  });

  it('calls both mobile chrome actions when the stacked control is used', () => {
    const mockUseModelEffort = vi.mocked(useModelEffort);
    mockUseModelEffort.mockReturnValue(defaultMockHook);

    render(
      <ChatHeader
        onReset={mockOnReset}
        onAbort={mockOnAbort}
        isGenerating={false}
        onToggleFileBrowser={mockOnToggleFileBrowser}
        isFileBrowserCollapsed={true}
        onToggleMobileTopBar={mockOnToggleMobileTopBar}
        isMobileTopBarHidden={true}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /show header controls/i }));
    fireEvent.click(screen.getByRole('button', { name: /open file explorer/i }));

    expect(mockOnToggleMobileTopBar).toHaveBeenCalledTimes(1);
    expect(mockOnToggleFileBrowser).toHaveBeenCalledTimes(1);
  });

  it('shows a truthful error and disables the model selector when no configured models are available', () => {
    const mockUseModelEffort = vi.mocked(useModelEffort);
    mockUseModelEffort.mockReturnValue({
      ...defaultMockHook,
      modelOptions: [],
      selectedModel: '',
      uiError: 'Could not load configured models',
    });

    render(
      <ChatHeader
        onReset={mockOnReset}
        onAbort={mockOnAbort}
        isGenerating={false}
      />
    );

    expect(screen.getByRole('status')).toHaveTextContent('Could not load configured models');
    expect(screen.getByRole('button', { name: 'Model' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Model' })).toHaveTextContent('No configured models');
  });

  it('uses the effort display label when provided by the hook', () => {
    const mockUseModelEffort = vi.mocked(useModelEffort);
    mockUseModelEffort.mockReturnValue({
      ...defaultMockHook,
      selectedEffort: 'thinkingDefault',
      selectedEffortLabel: 'medium',
      effortOptions: [
        { value: 'thinkingDefault', label: 'medium (default)' },
        { value: 'medium', label: 'medium' },
      ],
    });

    render(
      <ChatHeader
        onReset={mockOnReset}
        onAbort={mockOnAbort}
        isGenerating={false}
      />
    );

    expect(screen.getByRole('button', { name: 'Effort' })).toHaveTextContent('medium');
    expect(screen.getByRole('button', { name: 'Effort' })).not.toHaveTextContent('medium (default)');
  });

  it('shows abort button when generating', () => {
    const mockUseModelEffort = vi.mocked(useModelEffort);
    mockUseModelEffort.mockReturnValue(defaultMockHook);

    render(
      <ChatHeader
        onReset={mockOnReset}
        onAbort={mockOnAbort}
        isGenerating={true}
      />
    );

    expect(screen.getByRole('button', { name: /stop generating/i })).toBeInTheDocument();
  });

  it('shows the new-session button when not generating', () => {
    const mockUseModelEffort = vi.mocked(useModelEffort);
    mockUseModelEffort.mockReturnValue(defaultMockHook);

    render(
      <ChatHeader
        onReset={mockOnReset}
        onAbort={mockOnAbort}
        isGenerating={false}
      />
    );

    // Central Command semantics: the lever closes the current session (kept in
    // history) and starts fresh — never a destructive "reset".
    expect(screen.getByRole('button', { name: /new session/i })).toBeInTheDocument();
  });

  it('shows no source-email chip when the session was not spawned from mail', () => {
    const mockUseModelEffort = vi.mocked(useModelEffort);
    mockUseModelEffort.mockReturnValue(defaultMockHook);

    render(<ChatHeader onReset={mockOnReset} onAbort={mockOnAbort} isGenerating={false} />);

    expect(screen.queryByText(/Source email:/)).not.toBeInTheDocument();
  });

  it('renders a collapsed source-email chip that expands to sender and body', () => {
    const mockUseModelEffort = vi.mocked(useModelEffort);
    mockUseModelEffort.mockReturnValue(defaultMockHook);

    render(
      <ChatHeader
        onReset={mockOnReset}
        onAbort={mockOnAbort}
        isGenerating={false}
        sourceEmail={{
          id: 'wi_1',
          kind: 'email',
          subject: 'Reminder: Budget Ratification Meeting',
          payload: { from: 'hoa@example.com', text: 'please attend' },
        }}
      />
    );

    expect(screen.getByText(/Source email: Reminder: Budget Ratification Meeting/)).toBeInTheDocument();
    // Collapsed by default — the sender/body are not yet in the document.
    expect(screen.queryByText('hoa@example.com')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle('Source email'));

    expect(screen.getByText('hoa@example.com')).toBeInTheDocument();
    expect(screen.getByText('please attend')).toBeInTheDocument();
  });
});
