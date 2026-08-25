"""
Static geography data for onboarding: country → region → city/municipality.

Kept as bundled data (no runtime API) so the cascading dropdowns are instant.
Extend freely — this is presentation-level data, not business logic.
"""

COUNTRIES = {
    "SE": {"name": "Sweden", "flag": "🇸🇪", "query_language": "Swedish and English"},
    "GB": {"name": "United Kingdom", "flag": "🇬🇧", "query_language": "English"},
}

GEO: dict = {
    "SE": {
        "Blekinge län": ["Karlshamn", "Karlskrona", "Olofström", "Ronneby", "Sölvesborg"],
        "Dalarnas län": ["Avesta", "Borlänge", "Falun", "Gagnef", "Hedemora", "Leksand", "Ludvika", "Malung-Sälen", "Mora", "Orsa", "Rättvik", "Smedjebacken", "Säter", "Vansbro", "Älvdalen"],
        "Gotlands län": ["Gotland (Visby)"],
        "Gävleborgs län": ["Bollnäs", "Gävle", "Hofors", "Hudiksvall", "Ljusdal", "Nordanstig", "Ockelbo", "Ovanåker", "Sandviken", "Söderhamn"],
        "Hallands län": ["Falkenberg", "Halmstad", "Hylte", "Kungsbacka", "Laholm", "Varberg"],
        "Jämtlands län": ["Berg", "Bräcke", "Krokom", "Ragunda", "Strömsund", "Åre", "Östersund"],
        "Jönköpings län": ["Aneby", "Eksjö", "Gislaved", "Gnosjö", "Habo", "Jönköping", "Mullsjö", "Nässjö", "Sävsjö", "Tranås", "Vaggeryd", "Vetlanda", "Värnamo"],
        "Kalmar län": ["Borgholm", "Emmaboda", "Hultsfred", "Högsby", "Kalmar", "Mönsterås", "Nybro", "Oskarshamn", "Torsås", "Vimmerby", "Västervik"],
        "Kronobergs län": ["Alvesta", "Lessebo", "Ljungby", "Markaryd", "Tingsryd", "Uppvidinge", "Växjö", "Älmhult"],
        "Norrbottens län": ["Arjeplog", "Arvidsjaur", "Boden", "Haparanda", "Jokkmokk", "Kalix", "Kiruna", "Luleå", "Pajala", "Piteå", "Älvsbyn", "Överkalix", "Övertorneå"],
        "Skåne län": ["Bjuv", "Bromölla", "Burlöv", "Båstad", "Eslöv", "Helsingborg", "Hässleholm", "Höganäs", "Hörby", "Höör", "Klippan", "Kristianstad", "Kävlinge", "Landskrona", "Lomma", "Lund", "Malmö", "Osby", "Perstorp", "Simrishamn", "Sjöbo", "Skurup", "Staffanstorp", "Svalöv", "Svedala", "Tomelilla", "Trelleborg", "Vellinge", "Ystad", "Åstorp", "Ängelholm", "Örkelljunga", "Östra Göinge"],
        "Stockholms län": ["Botkyrka", "Danderyd", "Ekerö", "Haninge", "Huddinge", "Järfälla", "Lidingö", "Nacka", "Norrtälje", "Nykvarn", "Nynäshamn", "Salem", "Sigtuna", "Solna", "Stockholm", "Sundbyberg", "Södertälje", "Tyresö", "Täby", "Upplands-Bro", "Upplands Väsby", "Vallentuna", "Vaxholm", "Värmdö", "Österåker"],
        "Södermanlands län": ["Eskilstuna", "Flen", "Gnesta", "Katrineholm", "Nyköping", "Oxelösund", "Strängnäs", "Trosa", "Vingåker"],
        "Uppsala län": ["Enköping", "Heby", "Håbo", "Knivsta", "Tierp", "Uppsala", "Älvkarleby", "Östhammar"],
        "Värmlands län": ["Arvika", "Eda", "Filipstad", "Forshaga", "Grums", "Hagfors", "Hammarö", "Karlstad", "Kil", "Kristinehamn", "Munkfors", "Storfors", "Sunne", "Säffle", "Torsby", "Årjäng"],
        "Västerbottens län": ["Bjurholm", "Dorotea", "Lycksele", "Malå", "Nordmaling", "Norsjö", "Robertsfors", "Skellefteå", "Sorsele", "Storuman", "Umeå", "Vilhelmina", "Vindeln", "Vännäs", "Åsele"],
        "Västernorrlands län": ["Härnösand", "Kramfors", "Sollefteå", "Sundsvall", "Timrå", "Ånge", "Örnsköldsvik"],
        "Västmanlands län": ["Arboga", "Fagersta", "Hallstahammar", "Köping", "Norberg", "Sala", "Skinnskatteberg", "Surahammar", "Västerås"],
        "Västra Götalands län": ["Ale", "Alingsås", "Bengtsfors", "Bollebygd", "Borås", "Dals-Ed", "Essunga", "Falköping", "Göteborg", "Grästorp", "Gullspång", "Götene", "Herrljunga", "Härryda", "Karlsborg", "Kungälv", "Lerum", "Lidköping", "Lilla Edet", "Mariestad", "Mark", "Mellerud", "Munkedal", "Mölndal", "Partille", "Skara", "Skövde", "Sotenäs", "Stenungsund", "Strömstad", "Svenljunga", "Tanum", "Tibro", "Tidaholm", "Tjörn", "Tranemo", "Trollhättan", "Töreboda", "Uddevalla", "Ulricehamn", "Vara", "Vårgårda", "Vänersborg", "Åmål", "Öckerö"],
        "Örebro län": ["Askersund", "Degerfors", "Hallsberg", "Karlskoga", "Kumla", "Laxå", "Lekeberg", "Lindesberg", "Ljusnarsberg", "Nora", "Örebro"],
        "Östergötlands län": ["Åtvidaberg", "Boxholm", "Finspång", "Kinda", "Linköping", "Mjölby", "Motala", "Norrköping", "Söderköping", "Vadstena", "Valdemarsvik", "Ydre", "Ödeshög"],
    },
    "GB": {
        "Greater London": ["London", "Croydon", "Camden", "Islington", "Richmond", "Wimbledon", "Bromley", "Enfield"],
        "South East England": ["Brighton", "Oxford", "Reading", "Southampton", "Portsmouth", "Milton Keynes", "Guildford", "Canterbury", "Eastbourne", "Windsor"],
        "South West England": ["Bristol", "Bath", "Exeter", "Plymouth", "Bournemouth", "Gloucester", "Cheltenham", "Swindon", "Truro", "Taunton"],
        "West Midlands": ["Birmingham", "Coventry", "Wolverhampton", "Solihull", "Walsall", "Dudley", "Stoke-on-Trent", "Shrewsbury"],
        "East Midlands": ["Nottingham", "Leicester", "Derby", "Northampton", "Loughborough", "Lincoln", "Chesterfield", "Mansfield"],
        "East of England": ["Cambridge", "Norwich", "Ipswich", "Luton", "Peterborough", "Colchester", "Chelmsford", "Watford"],
        "North West England": ["Manchester", "Liverpool", "Preston", "Blackpool", "Bolton", "Bury", "Stockport", "Warrington", "Lancaster", "Chester"],
        "Yorkshire and the Humber": ["Leeds", "Sheffield", "York", "Hull", "Bradford", "Doncaster", "Harrogate", "Huddersfield", "Wakefield"],
        "North East England": ["Newcastle upon Tyne", "Sunderland", "Durham", "Middlesbrough", "Gateshead", "Darlington", "Stockton-on-Tees"],
        "Scotland": ["Edinburgh", "Glasgow", "Aberdeen", "Dundee", "Inverness", "Stirling", "Perth", "Falkirk"],
        "Wales": ["Cardiff", "Swansea", "Newport", "Wrexham", "Bangor", "Aberystwyth"],
        "Northern Ireland": ["Belfast", "Londonderry", "Lisburn", "Newry", "Armagh"],
    },
}
