import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { PersonCard, PersonFields } from './PersonDetails';
import { isPerson, parseAliases, personForm, personPatch, type PersonForm } from './graphUi';

const JANE = {
  labels: ['Entity', 'Person'], aliases: ['Jane', 'JRD'],
  birth_date: '1980-02-29', phone: '+1 555 0100',
};

describe('PersonCard', () => {
  it("shows a Person's other names, birth date and phone", () => {
    render(<PersonCard node={JANE} />);
    const card = screen.getByLabelText('Person details');
    expect(card).toHaveTextContent('Also called');
    expect(card).toHaveTextContent('Jane, JRD');
    expect(card).toHaveTextContent('Born');
    expect(card).toHaveTextContent('1980-02-29');
    expect(card).toHaveTextContent('+1 555 0100');
  });

  it('renders nothing when the person has none of them', () => {
    const { container } = render(<PersonCard node={{ aliases: [], birth_date: null, phone: null }} />);
    expect(container).toBeEmptyDOMElement();
  });
});

function Harness({ initial, seen }: { initial: PersonForm; seen: PersonForm[] }) {
  const [form, setForm] = useState(initial);
  return <PersonFields form={form} onChange={(f) => { seen.push(f); setForm(f); }} />;
}

describe('PersonFields', () => {
  it('edits aliases as a comma-separated line, the birth date and the phone', async () => {
    const seen: PersonForm[] = [];
    render(<Harness initial={personForm(JANE)} seen={seen} />);
    expect(screen.getByLabelText('Person aliases')).toHaveValue('Jane, JRD');
    expect(screen.getByLabelText('Person birth date')).toHaveValue('1980-02-29');
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('Person aliases'), ', Janie');
    await user.clear(screen.getByLabelText('Person phone'));
    await user.type(screen.getByLabelText('Person phone'), '555-0199');
    const last = seen[seen.length - 1];
    expect(last.aliases).toBe('Jane, JRD, Janie');
    expect(last.phone).toBe('555-0199');
    expect(personPatch(JANE, last)).toEqual({ aliases: ['Jane', 'JRD', 'Janie'], phone: '555-0199' });
  });
});

describe('personPatch', () => {
  it('sends only what changed, and an emptied field as a clear', () => {
    expect(personPatch(JANE, personForm(JANE))).toEqual({});
    expect(personPatch(JANE, { aliases: '', birthDate: '', phone: ' +1 555 0100 ' }))
      .toEqual({ aliases: [], birth_date: '' });
    expect(personPatch({ aliases: [], birth_date: null, phone: null },
      { aliases: ' Barb , ,Barbara ', birthDate: '1950-01-01', phone: '' }))
      .toEqual({ aliases: ['Barb', 'Barbara'], birth_date: '1950-01-01' });
  });

  it('parses aliases and recognises a Person', () => {
    expect(parseAliases(' a, ,b ,')).toEqual(['a', 'b']);
    expect(isPerson({ labels: ['Entity', 'Person'] })).toBe(true);
    expect(isPerson({ labels: ['Entity', 'Organization'] })).toBe(false);
  });
});
