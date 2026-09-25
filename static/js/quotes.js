// The day's quote for the dashboard hero.
//
// A list and the way it is read, and nothing else. The pick is *deterministic*
// -- the same day always shows the same quote -- because "a quote a day" that
// changed under a reload would be a quote per page load wearing a calendar's
// clothes. The day is the browser's own, the same clock the greeting and the
// time beside it read: picking from the server's date would hand you tomorrow's
// quote this evening, and that is the one thing a daily thing must not do.
//
// The text is kept here rather than sent down /api/home because it is not a
// fact about the vault -- the dashboard's cards are, and those stay on the
// server where they cannot disagree with the views they stand for.

// 15 figures, 6 quotes each, from Wikiquote, the Marxists Internet Archive and
// Goodreads. Grouped by figure so the list reads like a shelf; QUOTES below is
// the flat list the picker actually counts.
const FIGURES = [
  {
    name: "Ursula K. Le Guin",
    role: "American science fiction & fantasy author",
    quotes: [
      ["To light a candle is to cast a shadow.", "A Wizard of Earthsea (1968)"],
      ["We don't live in order to die, we live in order to live.", "Interview, Vice Magazine"],
      ["The untold story mothers the lie.", "Another Story or a Fisherman of the Inland Sea (1994)"],
      ["The only thing that makes life possible is permanent, intolerable uncertainty: not knowing what comes next.", "The Left Hand of Darkness (1969)"],
      ["To oppose something is to maintain it.", "The Left Hand of Darkness (1969)"],
      ["The unread story is not a story; it is little black marks on wood pulp. The reader, reading it, makes it live: a live thing, a story.", "Where Do You Get Your Ideas From? (1987)"],
    ],
  },
  {
    name: "Karl Marx",
    role: "German philosopher, economist, political theorist",
    quotes: [
      ["Religion is the sigh of the oppressed creature, the heart of a heartless world, and the soul of soulless conditions. It is the opium of the people.", "Critique of Hegel's Philosophy of Right, Introduction (1843)"],
      ["The philosophers have only interpreted the world, in various ways; the point is to change it.", "Theses On Feuerbach, Thesis 11 (1845)"],
      ["History does nothing, it 'possesses no immense wealth', it 'wages no battles'. It is man, real, living man who does all that.", "The Holy Family, Chapter 6 (1846)"],
      ["Revolutions are the locomotives of history.", "Class Struggle in France (1850)"],
      ["The great mass of the French nation is formed by the simple addition of homologous magnitudes, much as potatoes in a sack form a sack of potatoes.", "Eighteenth Brumaire of Louis Bonaparte (1852)"],
      ["The knell of capitalist private property sounds. The expropriators are expropriated.", "Capital, Volume I, Chapter 32 (1867)"],
    ],
  },
  {
    name: "Frida Kahlo",
    role: "Mexican painter, surrealist",
    quotes: [
      ["They thought I was a Surrealist, but I wasn't. I never painted dreams. I painted my own reality.", "Time magazine (April 27, 1953)"],
      ["I drank because I wanted to drown my sorrows, but now the damned things have learned to swim.", "Letter to Ella Wolfe (1938)"],
      ["I have suffered two grave accidents in my life, one in which a streetcar knocked me down... The other accident is Diego.", "Imagen de Frida Kahlo, Gisèle Freund (1951)"],
      ["I am not sick. I am broken. But I am happy to be alive as long as I can paint.", "Time magazine (April 27, 1953)"],
      ["Feet, what do I need them for if I have wings to fly.", "Diary illustration (1953)"],
      ["I hope the exit is joyful and I hope never to return.", "Last words in her diary (July 1954)"],
    ],
  },
  {
    name: "James Baldwin",
    role: "American novelist, essayist, civil rights activist",
    quotes: [
      ["Not everything that is faced can be changed; but nothing can be changed until it is faced.", "As Much Truth As One Can Bear, NYT Book Review (1962)"],
      ["Anyone who has ever struggled with poverty knows how extremely expensive it is to be poor.", "Fifth Avenue, Uptown, Esquire (1960)"],
      ["Money, it turned out, was exactly like sex. You thought of nothing else if you didn't have it and thought of other things if you did.", "Nobody Knows My Name (1961)"],
      ["The primary distinction of the artist is that he must actively cultivate that state which most men, necessarily, must avoid: the state of being alone.", "The Creative Process (1962)"],
      ["You don't realize that you're intelligent until it gets you into trouble.", "Interview with Julius Lester, NYT (1984)"],
      ["Time is just common, it's like water for a fish. Everybody's in this water, nobody gets out of it.", "Giovanni's Room (1956)"],
    ],
  },
  {
    name: "bell hooks",
    role: "American scholar, feminist author",
    quotes: [
      ["Feminism is a movement to end sexism, sexist exploitation, and oppression.", "Feminism Is for Everybody (2014)"],
      ["The first act of violence that patriarchy demands of males is not violence toward women. Instead patriarchy demands of all males that they engage in acts of psychic self-mutilation, that they kill off the emotional parts of themselves.", "The Will to Change (2004)"],
      ["The crisis facing men is not the crisis of masculinity, it is the crisis of patriarchal masculinity.", "The Will to Change (2004)"],
      ["The moment we choose to love we begin to move against domination, against oppression. The moment we choose to love we begin to move towards freedom, to act in ways that liberate ourselves and others.", "Outlaw Culture (2006)"],
      ["People with healthy self-esteem do not need to create pretend identities.", "Rock My Soul (2003)"],
      ["To be in touch with senses and emotions beyond conquest is to enter the realm of the mysterious.", "Outlaw Culture (2006)"],
    ],
  },
  {
    name: "Douglas Adams",
    role: "English science fiction author, humorist",
    quotes: [
      ["I love deadlines. I love the whooshing noise they make as they go by.", "The Salmon of Doubt (2002)"],
      ["We are stuck with technology when what we really want is just stuff that works.", "The Salmon of Doubt (2002)"],
      ["If you try and take a cat apart to see how it works, the first thing you have on your hands is a nonworking cat.", "The Salmon of Doubt (2002)"],
      ["A learning experience is one of those things that say, 'You know that thing you just did? Don't do that.'", "Interview, The Daily Nexus (2000)"],
      ["I'd take the awe of understanding over the awe of ignorance any day.", "The Salmon of Doubt (2002)"],
      ["It was a joke. It had to be a number, an ordinary, smallish number, and I chose that one. I sat at my desk, stared into the garden and thought '42 will do.'", "alt.fan.douglas-adams, USENET (1993)"],
    ],
  },
  {
    name: "Mark Fisher",
    role: "British cultural theorist, blogger (k-punk), author of Capitalist Realism",
    quotes: [
      ["Capitalist realism is therefore not a particular type of realism; it is more like realism in itself.", "Capitalist Realism, Chapter One"],
      ["No cultural object can retain its power when there are no longer new eyes to see it.", "Capitalist Realism, Chapter One"],
      ["The role of capitalist ideology is not to make an explicit case for something in the way that propaganda does, but to conceal the fact that the operations of capital do not depend on any sort of subjectively assumed belief.", "Capitalist Realism, Chapter Two"],
      ["A culture that is merely preserved is no culture at all.", "Capitalist Realism, Chapter One"],
      ["In capitalism, all that is solid melts into PR.", "Capitalist Realism, Chapter Six"],
      ["Capitalist realism can only be threatened if it is shown to be in some way inconsistent or untenable.", "Capitalist Realism, Chapter Three"],
    ],
  },
  {
    name: "David Graeber",
    role: "American anthropologist, anarchist, author of Debt and Bullshit Jobs",
    quotes: [
      ["We did not begin with barter, discover money, and then eventually develop credit systems. It happened precisely the other way around.", "Debt: The First 5,000 Years (2011)"],
      ["Direct action is the insistence, when faced with structures of unjust authority, on acting as if one is already free.", "Direct Action: An Ethnography (2009)"],
      ["What is a debt, anyway? A debt is just the perversion of a promise. It is a promise corrupted by both math and violence.", "Debt: The First 5,000 Years (2011)"],
      ["The ultimate, hidden truth of the world is that it is something that we make, and could just as easily make differently.", "The Utopia of Rules (2015)"],
      ["Hell is a collection of individuals who spend their time working on a task they don't like and are not especially good at.", "Bullshit Jobs (2013)"],
      ["Every time you reach an agreement by consensus, rather than threats... you are being an anarchist — even if you don't realize it.", "Are You An Anarchist? (2000)"],
    ],
  },
  {
    name: "Thomas Pynchon",
    role: "American postmodern novelist, author of Gravity's Rainbow",
    quotes: [
      ["Why should things be easy to understand?", "Interview, Playboy (1977)"],
      ["If they can get you asking the wrong questions, they don't have to worry about answers.", "Gravity's Rainbow (1973)"],
      ["Paranoia's the garlic in life's kitchen, right, you can never have too much.", "Bleeding Edge (2013)"],
      ["Let me be unambiguous. I prefer not to be photographed.", "Phone call to CNN (1997)"],
      ["All investigations of Time, however sophisticated or abstract, have at their true base the human fear of mortality.", "Against the Day (2006)"],
      ["History is not Chronology, for that is left to lawyers, — nor is it Remembrance, for Remembrance belongs to the People.", "Mason & Dixon (1997)"],
    ],
  },
  {
    name: "William S. Burroughs",
    role: "American Beat author, counterculture figure, author of Naked Lunch",
    quotes: [
      ["Most of the trouble in the world has been caused by ten to twenty percent of folks who can't mind their own business, because they have no business of their own to mind, any more than a smallpox virus.", "The Place of Dead Roads (1983)"],
      ["The junk merchant doesn't sell his product to the consumer, he sells the consumer to his product.", "Letter from a Master Addict to Dangerous Drugs (1956)"],
      ["From symbiosis to parasitism is a short step. The word is now a virus.", "The Ticket That Exploded (1962)"],
      ["Control can never be a means to anything but more control... like Junk.", "Islam Incorporated and the Parties of Interzone"],
      ["This is a war universe. War all the time. That is its nature.", "The War Universe, Grand Street (1991)"],
      ["Cut word lines — Cut music lines — Smash the control images — Smash the control machine.", "The Soft Machine (1961)"],
    ],
  },
  {
    name: "China Miéville",
    role: "British weird fiction author, socialist, author of Perdido Street Station",
    quotes: [
      ["I want to have monsters as a metaphor but I also want monsters because monsters are cool. There's no contradiction.", "Interview with 3am"],
      ["I prefer to think of it as a quantum Hugo and that Paolo Bacigalupi and I oscillate between Hugo particle and wave form. So it's properly science-fictional.", "On winning the Hugo Award (2010)"],
      ["What if the chosen one misunderstands what he's been chosen for?", "The Tain, Looking for Jake (2005)"],
      ["There are no cats in UnLondon, for example, because they're not magic and mysterious at all, they're idiots.", "Un Lun Dun (2007)"],
      ["Destiny's bunk. From here on in, we're the Order of Suggesters.", "Un Lun Dun (2007)"],
      ["It should be illegal to be so much younger than me.", "Kraken: An Anatomy (2010)"],
    ],
  },
  {
    name: "Samuel R. Delany",
    role: "American science fiction author, critic, author of Dhalgren and Babel-17",
    quotes: [
      ["The science of probability gives mathematical expression to our ignorance, not to our wisdom.", "Time Considered as a Helix of Semi-Precious Stones (1968)"],
      ["The emblem of a philosophy is not that it contains a set of specific thoughts, but that it generates a way of thinking.", "Triton (1976)"],
      ["The only important elements in any society are the artistic and the criminal, because they alone, by questioning the society's values, can force it to change.", "Empire Star (1966)"],
      ["Don't go chattering to the stars if you're going to do it with your eyes closed.", "Nova (1968)"],
      ["No man can wield absolute power over other men and still retain his own mind.", "The Jewels of Aptor (1962)"],
      ["Childhood is that time in which we never question the fact that every adult act is filled with meaning. Adulthood is that time in which we see that all human actions follow forms.", "Tales of Nevèrÿon (1979)"],
    ],
  },
  {
    name: "Hakim Bey",
    role: "American anarchist poet, author of TAZ: The Temporary Autonomous Zone",
    quotes: [
      ["Provided we can escape from the museums we carry around inside us, provided we can stop selling ourselves tickets to the galleries in our own skulls, we can begin to contemplate an art which re-creates the goal of the sorcerer: changing the structure of reality by the manipulation of living symbols. Art tells gorgeous lies that come true.", "TAZ: The Temporary Autonomous Zone"],
      ["Don't just survive while waiting for someone's revolution to clear your head.", "TAZ: The Temporary Autonomous Zone"],
      ["Words belong to those who use them only till someone else steals them back.", "TAZ: The Temporary Autonomous Zone"],
      ["Chaos comes before all principles of order & entropy, it's neither a god nor a maggot, its idiotic desires encompass & define every possible choreography.", "TAZ: The Temporary Autonomous Zone"],
      ["The sorcerer is a Simple Realist: the world is real — but then so must consciousness be real since its effects are so tangible.", "TAZ: The Temporary Autonomous Zone"],
      ["Shall we not confess that the politics of that night have more reality and force for us than those of, say, the entire U.S. Government?", "TAZ: The Temporary Autonomous Zone"],
    ],
  },
  {
    name: "Kathy Acker",
    role: "American experimental novelist, punk and feminist writer",
    quotes: [
      ["Every book, remember, is dead until a reader activates it by reading. Every time that you read you are walking among the dead, and, if you are listening, you just might hear prophecies.", "On Delany the Magician, foreword (1996)"],
      ["You create identity, you're not given identity per se. What became more interesting to me wasn't the I, it was text because it's texts that create the identity.", "Hannibal Lecter, My Father (1991)"],
      ["The only characteristic freaks share is our knowledge that we don't fit in.", "Don Quixote (1986)"],
      ["The German Romantics had to destroy the same bastions we do. Logocentrism and idealism, theology, all supports of the repressive society.", "Empire of the Senseless (1988)"],
      ["I'm very staid compared to my students, actually. I come from a generation where you've got the PC dykes and confused heterosexuals.", "Interview, io (1997)"],
      ["If there is a God, God is disjunction and madness.", "Blood and Guts in High School (1978)"],
    ],
  },
  {
    name: "M. John Harrison",
    role: "British author of weird fiction and literary fiction, author of Light and Climbers",
    quotes: [
      ["Identity is not negotiable. An identity you have achieved by agreement is always a prison.", "Things That Never Happen"],
      ["Worldbuilding is dull. Worldbuilding literalises the urge to invent.", "Unsourced"],
      ["Perception of a state is not the state.", "Nova Swing"],
      ["The worst thing in the world is to be inside yourself, you don't even want to be rescued.", "Unsourced"],
      ["Everyone loves a mysterious country.", "Things That Never Happen"],
      ["Happiness and beauty are the worst things you can have in a life, because you never forget them. They go on and on ambushing you, presumably until you die.", "Unsourced"],
    ],
  },
];

//: Every quote, in one list, with its figure attached. This is what the picker
//: counts -- a quote does not know which figure it came from once it is flat,
//: so the name (and the role, for the tooltip) travels with it.
export const QUOTES = FIGURES.flatMap((figure) =>
  figure.quotes.map(([text, source]) => ({
    text,
    source,
    name: figure.name,
    role: figure.role,
  })));

// FNV-1a over the day key. Any hash would do; this one is five lines and its
// avalanche is good enough that consecutive dates land on unrelated quotes
// rather than walking the list one entry at a time.
function hash(text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

//: "2026-09-25", from the local clock. Built from the parts rather than from
//: toISOString(), which would convert to UTC first -- that is how a quote
//: rotates at 7pm the evening before.
export function dayKey(now) {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

// The day before a key, for the one rule the hash cannot promise on its own.
// Date knows how months and leap years end, so this stays three lines.
function dayBefore(key) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return null;
  const [y, m, d] = key.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  date.setDate(date.getDate() - 1);
  return dayKey(date);
}

// The quote for a day, keyed by its date. Deterministic: no clock, no random,
// no state, so the same day cannot show two different quotes.
export function quoteForDay(key, list = QUOTES) {
  const size = list.length;
  if (!size) return null;
  const index = hash(key) % size;
  // Two mornings running should not say the same thing: a repeat is the one way
  // a daily rotation reads as broken. This looks back exactly one day -- the
  // ordinary collision, where both dates pick the same quote.
  //
  // One day and not a chain, deliberately: asking what yesterday *showed*
  // needs what the day before showed, and that question has no bottom. The
  // price is the coincidence the test measures -- a repeat when three days
  // running pick the same quote, about one day in eight thousand, which is
  // rarer than the hash's own bad luck and reads as nothing at all.
  const before = dayBefore(key);
  if (before !== null && size > 1 && hash(before) % size === index) {
    return list[(index + 1) % size];
  }
  return list[index];
}

export function quoteOfTheDay(now = new Date()) {
  return quoteForDay(dayKey(now));
}

// "— Ursula K. Le Guin, A Wizard of Earthsea (1968)". The source is set off by
// a comma rather than a separator glyph because sources already carry their own
// punctuation and a second mark reads as part of the title.
export function attribution(quote) {
  if (!quote) return "";
  return quote.source ? `— ${quote.name}, ${quote.source}` : `— ${quote.name}`;
}
