import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FieldRow } from '../IntegrationsHub';
import type { IntegrationFieldDef } from '../../../services/api/settings';

const modelField: IntegrationFieldDef = {
  key: 'model',
  label: 'Default Model',
  secret: false,
  placeholder: 'claude-haiku-4-5',
  optional: true,
};

const baseProps = {
  field: modelField,
  value: '',
  hasStored: false,
  showValue: false,
  onToggleShow: () => {},
  onChange: () => {},
};

describe('IntegrationsHub FieldRow - Anthropic model selector', () => {
  it('renders a <select> populated from the live model list', () => {
    render(
      <FieldRow
        {...baseProps}
        selectOptions={[
          { id: 'claude-opus-4-8', display_name: 'Claude Opus 4.8' },
          { id: 'claude-sonnet-4-6', display_name: 'Claude Sonnet 4.6' },
        ]}
      />,
    );
    const select = screen.getByRole('combobox');
    expect(select).toBeInTheDocument();
    // display_name shown, value = id
    expect(screen.getByRole('option', { name: 'Claude Opus 4.8' })).toHaveValue('claude-opus-4-8');
    // "Use default" clear option is present
    expect(screen.getByRole('option', { name: 'Use default' })).toHaveValue('');
  });

  it('pre-selects the currently-saved model', () => {
    render(
      <FieldRow
        {...baseProps}
        value="claude-sonnet-4-6"
        selectOptions={[
          { id: 'claude-opus-4-8', display_name: 'Claude Opus 4.8' },
          { id: 'claude-sonnet-4-6', display_name: 'Claude Sonnet 4.6' },
        ]}
      />,
    );
    expect(screen.getByRole('combobox')).toHaveValue('claude-sonnet-4-6');
  });

  it('keeps a saved-but-retired model selectable so the value is not dropped', () => {
    render(
      <FieldRow
        {...baseProps}
        value="claude-retired-x"
        selectOptions={[{ id: 'claude-sonnet-4-6', display_name: 'Claude Sonnet 4.6' }]}
      />,
    );
    const select = screen.getByRole('combobox');
    expect(select).toHaveValue('claude-retired-x');
    expect(screen.getByRole('option', { name: 'claude-retired-x (saved)' })).toBeInTheDocument();
  });

  it('emits the chosen model id on change', () => {
    const onChange = vi.fn();
    render(
      <FieldRow
        {...baseProps}
        onChange={onChange}
        selectOptions={[
          { id: 'claude-opus-4-8', display_name: 'Claude Opus 4.8' },
          { id: 'claude-sonnet-4-6', display_name: 'Claude Sonnet 4.6' },
        ]}
      />,
    );
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'claude-opus-4-8' } });
    expect(onChange).toHaveBeenCalledWith('claude-opus-4-8');
  });

  it('falls back to a free-text input when the model list is empty/unavailable', () => {
    render(<FieldRow {...baseProps} selectOptions={null} />);
    // No dropdown; a text input is rendered instead so the field stays usable.
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  it('falls back to free-text when selectOptions is an empty array', () => {
    render(<FieldRow {...baseProps} selectOptions={[]} />);
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });
});
