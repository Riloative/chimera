# English → Lithuanian glossary

Register: the polite plural (**jūs**) in all instructions and system messages
("Pasirinkite…", "Bandykite dar kartą"). Keep wording short and neutral.
Typography: Lithuanian quotation marks „…“, en dash with spaces ( – ) for
breaks, `el. paštas` abbreviation.

| English | Lithuanian | Notes |
| --- | --- | --- |
| chat, conversation | pokalbis | "New chat" → *Naujas pokalbis* |
| message | žinutė | |
| settings | nustatymai | |
| model | modelis | |
| prompt (reusable instruction) | užklausa | library → *užklausų biblioteka* |
| system prompt | sistemos užklausa | |
| knowledge base | žinių bazė | nav item "Knowledge" → *Žinios* |
| collection | rinkinys | *žinių rinkinys* |
| document | dokumentas | |
| chunk | fragmentas | |
| excerpt (retrieved passage) | ištrauka | |
| attachment | priedas | |
| connection (to a server) | ryšys | plural *ryšiai* |
| shared connection | bendras ryšys | |
| sign in | prisijungti | |
| sign out | atsijungti | |
| register / create account | registruotis / sukurti paskyrą | |
| account | paskyra | |
| password | slaptažodis | |
| login name | prisijungimo vardas | |
| email | el. paštas | |
| administrator | administratorius | |
| user | naudotojas | (not *vartotojas*) |
| session | seansas | |
| token (LLM) | žetonas | "tok/s" → *žet./s* |
| regenerate | generuoti iš naujo | |
| stop | sustabdyti | |
| embeddings | įterpiniai | |
| reasoning | samprotavimas | |
| inference | išvados | |
| endpoint | galinis taškas | |
| slot (llama-server) | lizdas | |
| seed | sėkla | |
| timeout | laukimo laikas | |
| vision (image input) | vaizdai / vaizdų atpažinimas | |
| web search | paieška internete | toggle label: *Paieška* |
| search engine | paieškos sistema | |
| search query | paieškos užklausa | (see *užklausa* for prompt below) |
| source | šaltinis | plural *šaltiniai*, gen. pl. *šaltinių* |
| citation | nuoroda į šaltinį | aria-label "{n} šaltinis: …" |
| link (URL) | nuoroda | |
| read a page / read again | perskaityti puslapį / perskaityti iš naujo | "Reading 4 sources" → *Skaitoma 4 šaltinių*… with plural forms |
| page | puslapis | |
| search result | paieškos rezultatas | |
| snippet / summary | santrauka | "Summary only" → *Tik santrauka* |
| context window | konteksto langas | "context limit" → *konteksto riba* |
| web access | prieiga prie interneto | Settings section "Web" → *Internetas* |
| allow-list / deny-list | leidžiamų sąrašas / „niekada neskaityti“ | phrased as full sentences in the UI |
| user agent | naudotojo agentas | |
| port | prievadas | |
| robots.txt | robots.txt | not translated |
| branch (of a conversation) | šaka | "this and all its branches" → *visose šakose* |
| version (of a message) | versija | navigator: *1 versija iš 2*, *Atsakymas 1 iš 2* |
| pin to context | prisegti prie konteksto | badge *Prisegta* |
| exchange (user message + answer) | apsikeitimas | |
| leave out (history) | praleisti | marker: *neįtraukta į modelio kontekstą* |
| summarize / summary | apibendrinti / santrauka | |
| tokenizer | žetonų skaidytuvas | |
| draft | juodraštis | |
| headless browser | foninė naršyklė | literally "background browser" |
| render (a page) | atvaizduoti | "Render with browser" → *Atvaizduoti naršykle* |
| release (a published version) | leidimas | "release notes" → *pakeitimų aprašas* |
| update (noun / verb) | atnaujinimas / atnaujinti | "Check for updates" → *Tikrinti, ar yra atnaujinimų* |
| version | versija | "Chimera 1.1.0 is available" → *Išleista Chimera 1.1.0* |
| changelog | pakeitimų žurnalas | the file; per-release text is *pakeitimų aprašas* |
| build (of the software) | versijos komplektas | Settings → About; "local build" → *vietinis komplektas* |
| license | licencija | |
| backup / restore | atsarginė kopija / atkurti | |
| roll back | atšaukti (atnaujinimą) | |

## Phrases that deserve native-speaker review

These were the least certain choices:

- **"užklausa" for prompt** – also means "query/request"; *raginimas* or the
  loanword *promptas* are alternatives.
- **"ryšys" for connection** – *jungtis* is a plausible alternative.
- **"žetonas" / "žet./s" for LLM tokens** – technical writers sometimes keep
  *tokenas*.
- **"įterpiniai" for embeddings**, **"išvados" for inference**,
  **"lizdai" for slots**, **"sėkla" for seed** – established in academic usage
  but may read as unusual to users.
- **Sampling parameter names** ("Bauda už kartojimą", "Bauda už buvimą",
  "Bauda už dažnumą", "Branduolio atrankos tikimybių suma").
- **Greetings** "Laba diena" (afternoon) and "Dirbate vėlai" (late night).

Web reading and search (0.3), additionally:

- **"užklausa"** now appears both as *prompt* (library) and in *paieškos
  užklausa* (search query); a reviewer may prefer *raginimas* for prompts to
  keep them apart.
- **"nuoroda"** is used for a link and in *nuoroda į šaltinį* (citation).
- **Plural sentences with two numbers**, e.g. *"Dėl modelio konteksto ribos
  panaudota 2 iš 3 šaltinių"* (the plural form follows the second number), and
  *"Nepavyko perskaityti 1 šaltinio – panaudota paieškos santrauka"*.
- **"Bus perskaityta ne daugiau kaip 5 nuorodų"** (limit hint) and
  **"Skaitoma 5 šaltinių"** (impersonal *skaitoma* with the genitive plural).
- **Technical terms:** *naudotojo agentas* (user agent), *prievadas* (port),
  *vietos rezervavimo ženklai* (placeholders), *nuorašai* (transcripts),
  *debesijos metaduomenų adresai* (cloud metadata addresses).
- **Privacy notice wording** (Settings → Web, first use of search): accuracy
  matters more than style here.
- **"many" plural forms** (e.g. "1,5 pokalbio") – only reachable with fractional
  counts, which the UI never produces, but provided for completeness.
- **Mixed constructions with interpolated names**, e.g. "Ryšys „{{name}}“
  išjungtas", "{{name}} nepasiekiamas" – grammatical gender/case of the
  interpolated name can't be inflected automatically.

Branches, context management and the headless browser (0.4), additionally:

- **"foninė naršyklė" for headless browser** – a descriptive choice;
  *begalvė naršyklė* (literal) or *naršyklė be sąsajos* are alternatives, and
  admins may know the English term better.
- **"atvaizduoti" for render** ("Atvaizduoti naršykle", "Atvaizduota foninės
  naršyklės") – *pateikti* or *įkelti* are possible; check the instrumental
  *naršykle* reads naturally on a button.
- **"šaka" for branch** and **"versija"** in *"1 versija iš 2"* (word order
  with the numeral first).
- **"apsikeitimas" for an exchange** (a question with its answer) – may read
  as unusual; *klausimas ir atsakymas* is longer but plainer.
- **"prisegti prie konteksto" for pin to context**, distinct from pinning a
  conversation in the sidebar (*prisegti*).
- **"žetonų skaidytuvas" for tokenizer** – *tokenizatorius* is the common
  loanword.
- **Marker sentences with plural agreement**, e.g. *"3 ankstesnės žinutės
  neįtrauktos į modelio kontekstą"*, *"12 ankstesnių žinučių neįtraukta…"*,
  *"Kad tilptų puslapiai, praleista 12 ankstesnių žinučių"*.
- **Delete confirmation** *"Bus pašalintos 3 žinutės: ši ir viskas, kas iš jos
  tęsiasi, visose šakose."* – for counts in the *one* category above 1 (21,
  31…) the shorter form without the explanation is used.
- **Error texts with numbers**: *"…jai reikia maždaug {{needed}} žetonų, o
  laisvų yra tik {{available}}"* – the genitive plural after numbers ending in
  1 (21 žetonas) is not inflected.
- **Admin status lines** for the browser service ("Nesukonfigūruota. Paleiskite
  browser profilį…") mix Lithuanian with untranslated configuration names.

Sources list, browser and release (0.4.1 and 1.0), additionally:

- **"Baigiantis laikui puslapis vis dar rodė įkėlimo ekraną, todėl jo
  perskaityti nepavyko."** (`web.browser_loading`) – *įkėlimo ekranas* for
  "loading screen".
- **"Išleista Chimera {{version}}."** for "Chimera X is available" – a
  reviewer may prefer *Galima Chimera {{version}}* or *Yra nauja versija*.
- **"leidimas"** for release vs. **"versija"** for version, and
  **"pakeitimų aprašas"** for release notes (vs. *pakeitimų žurnalas* for the
  changelog file).
- **"Versijos komplektas" / "vietinis komplektas"** for build / local build –
  the least natural choice; *Kompiliacija* or simply *Komitas* are alternatives.
- **"Tikrinti, ar yra atnaujinimų"**, **"Tikrinti kasdien"**, **"Ne, ačiū"**
  and the first-run question text (*askBody*), which carries the privacy promise.
- **"Paskutinis tikrinimas nepavyko ({{when}}): {{reason}}. Bus bandoma rytoj;
  naudotojai to nemato."**

UI polish (1.1), additionally:

- **"Rodyti {{count}} ankstesnę žinutę / ankstesnes žinutes / ankstesnių
  žinučių"** (`chat.earlier_*`) for "Show N earlier messages" – *ankstesnės*
  vs. *senesnės* (older); the *many* form (*ankstesnės žinutės*) is only used
  for fractions and never appears with whole counts.
- **"Rodyti senesnius pokalbius"** (`sidebar.showOlder`) for "Show older
  conversations".
- **"Paieška internete šiai žinutei"** (`web.composer.searchTooltip`, the
  accessible name of the web-search button): it must start with the visible
  label *Paieška* (WCAG 2.5.3), which makes it a noun phrase; the previous
  *"Ieškoti internete šiai žinutei"* read more naturally.

Web search per conversation, Auto and polish (1.2), additionally:

- **"Paieška internete šiame pokalbyje"** / **"Auto paieška internete šiame
  pokalbyje"** (`web.composer.searchName.*`): the accessible names must start
  with the visible word (*Paieška*, *Auto*), hence the noun phrases.
- **"Auto"** kept as is for the mode name (like the English); *Automatinė* or
  *Savarankiška* are alternatives if a reviewer prefers a Lithuanian word.
- **"Kitai žinutei neieškoti"** / **"Ieškoti internete kitai žinutei"** /
  **"Atšaukti pasirinkimą kitai žinutei"** for the one-off choices; *kitai
  žinutei* ("for the next message") vs. *tik šiai žinutei*.
- **"Paieška internete nereikalinga: {{reason}}"** — the reason comes from the
  model and may be in another language than the interface.
- **"Nepavyko nuspręsti, ar ieškoti, todėl atsakymas parašytas be interneto."**
- **"Leisti Auto paiešką internete"** and its help text (administrators).
- **"Nepavyko įkelti."** / **"Bandyti dar kartą"** (failed loads).
