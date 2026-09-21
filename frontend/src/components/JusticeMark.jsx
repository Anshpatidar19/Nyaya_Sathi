/* The Nyaya Sathi assistant mark: Lady Justice, from the reference bronze.

   The artwork is a real vector trace of the statue, not a redraw. The
   photograph is split into eight luminance bands, each traced separately
   and stacked darkest first, with every fill sampled from the photograph
   itself - hue untouched, saturation lifted, value pulled down. Eight
   bands rather than three is what makes it read as cast metal instead of
   as modelling clay, and sampled colour is what keeps the bronze from
   drifting to chocolate.

   It loads as an asset rather than inlining ~190KB of path data into the JS
   bundle. Vite fingerprints and caches it, the browser decodes it once
   however many times it appears, and the chat bundle stays the size it
   was. On the wire it gzips to about 50KB, comparable to a medium PNG,
   and unlike a PNG it stays sharp at every size.
   The cost is that CSS cannot reach inside it to recolour anything - which
   is correct here. A logo should not change hue with the theme toggle.

   Sizing is by HEIGHT. The figure is roughly 1:2.1, so a square box would
   either crop it or strand it in empty space.

     <JusticeMark height={220} decorative />   side accent, full detail
     <JusticeMark height={44} />               avatar beside a reply
     <JusticeMark variant="bust" size={64} />  round badge / launcher

   Below about 40px the scales and the sword begin to merge. For anything
   smaller, use a plain scales glyph rather than shrinking this further.

   The "bust" variant exists because the full figure cannot fill a circle.
   She is roughly 1:2.1, so dropping her whole into a 64px disc leaves a
   thin dark sliver with empty air either side - it reads as a smudge, not
   as a statue. The top of the statue is nearly square (raised arm, the
   scale beam, head and shoulders), so the badge scales her up and crops to
   that, the way a portrait medallion would. Same asset, no second file:
   the crop is a circular window with overflow hidden. */

import ladyJustice from '../assets/lady-justice.svg';

export default function JusticeMark({
  height = 200,
  size = 64,
  variant = 'full',
  className = '',
  decorative = false,
}) {
  if (variant === 'bust') {
    return (
      <span
        className={`jm-bust ${className}`}
        style={{
          display: 'block',
          width: size,
          height: size,
          borderRadius: '50%',
          overflow: 'hidden',
          // Bronze disc, so the statue's own dark tones have something to
          // sit against. Flat surface colours let her edges dissolve.
          background: 'linear-gradient(150deg, #3A2A18, #241A0F)',
          flexShrink: 0,
        }}
      >
        <img
          src={ladyJustice}
          style={{
            // Slightly oversized and nudged, so the scale beam clears the
            // left edge of the circle instead of being clipped by it.
            width: '110%',
            marginLeft: '-5%',
            marginTop: '4%',
            display: 'block',
          }}
          alt={decorative ? '' : 'Nyaya Sathi assistant'}
          aria-hidden={decorative ? 'true' : undefined}
          draggable="false"
        />
      </span>
    );
  }

  return (
    <img
      src={ladyJustice}
      className={className}
      style={{ height, width: 'auto', display: 'block' }}
      /* A decorative accent beside a panel should be invisible to a screen
         reader: the panel already has a name, and "Nyaya Sathi assistant"
         announced before every reply is noise. */
      alt={decorative ? '' : 'Nyaya Sathi assistant'}
      aria-hidden={decorative ? 'true' : undefined}
      draggable="false"
    />
  );
}