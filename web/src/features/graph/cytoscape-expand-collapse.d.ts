/** cytoscape-expand-collapse 4.x ships no types and has no @types package
 *  (checked 2026-08-30). Declaring the registration function is enough — the
 *  api object is typed at the call site in GraphView. */
declare module 'cytoscape-expand-collapse' {
  const register: (cy: unknown) => void;
  export default register;
}
