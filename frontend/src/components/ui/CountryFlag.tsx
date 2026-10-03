export default function CountryFlag({ code }: { code?: string | null }) {
  const country = code?.toUpperCase();
  if (!country || !/^[A-Z]{2}$/.test(country)) return <span title="Страна ещё не определена" aria-label="Страна ещё не определена">🌐</span>;
  const flag = country.split('').map(letter => String.fromCodePoint(127397 + letter.charCodeAt(0))).join('');
  const stripes: Record<string, string[]> = { DE: ['#111', '#d00', '#ffce00'], NL: ['#ae1c28', '#fff', '#21468b'], RU: ['#fff', '#0039a6', '#d52b1e'], FR: ['#002395', '#fff', '#ed2939'], IT: ['#009246', '#fff', '#ce2b37'], IE: ['#169b62', '#fff', '#ff883e'], AT: ['#ed2939', '#fff', '#ed2939'] };
  const colors = stripes[country];
  const vertical = ['FR', 'IT', 'IE'].includes(country);
  return <span className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-neutral-100 px-1.5 py-0.5 text-xs font-medium" title={`Страна выхода: ${country}`} aria-label={`Страна выхода: ${country}`}>
    {colors ? <svg aria-hidden="true" viewBox="0 0 30 20" className="h-3.5 w-5 rounded-sm ring-1 ring-black/10">{colors.map((color, index) => <rect key={index} fill={color} x={vertical ? index * 10 : 0} y={vertical ? 0 : index * 20 / 3} width={vertical ? 10 : 30} height={vertical ? 20 : 20 / 3} />)}</svg> : <span aria-hidden="true">{flag}</span>}
    <span>{country}</span>
  </span>;
}
