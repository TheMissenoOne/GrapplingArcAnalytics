# Fila Q6 — lutas unilaterais para re-leitura (guard/pass)

Gerada 2026-08-24 do banco. Critério: sequência inteira com um único `actor_id` e >=6
eventos — a convenção de ator (guard = quem joga guarda) foi REFUTADA nesses lotes
(medição Batch 1, docs/match_event_model.md): tudo foi arquivado sob uma atleta só.
Re-ler exige passar a transcrição de novo pelo refinador (DeepSeek, manual, luta a luta)
ou, quando houver footage, o caminho de frames. NÃO é automatizável — esta fila é o
produto executável da decisão Q6.

Total: 133 lutas, 2233 eventos retidos pelo portão de atribuição.

| # | luta | evento | ano | eventos | vídeo? |
|---|---|---|---|---|---|
| 1 | Andy Roberts × Jota Viklander | Polaris 36 | 2026 | 73 | — |
| 2 | Jared Eaton × Arren Gurnhill | Polaris 31 | 2025 | 58 | — |
| 3 | Gordon Ryan × Patrick Gaudio | WNO 20 | 2023 | 50 | sim |
| 4 | Matthew Korsak × Peter Shamarkou | Polaris 29 | 2024 | 48 | — |
| 5 | Mica Galvão × J. Rodriguez | WNO 20 | 2023 | 46 | — |
| 6 | Callen O'Mahony × Alex Aston | Polaris 35 | 2026 | 41 | — |
| 7 | Diogo Reis × K. Krikorian | WNO 20 | 2023 | 40 | — |
| 8 | Kenta Iwamoto × Nick Rodriguez | CJI 2 - Day 2 | 2025 | 39 | — |
| 9 | Charles Reindorf × Arren Gurnhill | Polaris 28 | 2024 | 39 | — |
| 10 | Archer Colaco × Franck Takoudjou | Polaris 26 | 2023 | 37 | — |
| 11 | Vagner Rocha × Kaynan Duarte | CJI 2 - Day 2 | 2025 | 37 | — |
| 12 | Casey Hellenberg × Carr Darabedian | EBI 14 | 2017 | 37 | — |
| 13 | Romao Carvalho × Muhammed Mustafa | Polaris 30 | 2024 | 36 | — |
| 14 | Kyle O'Hare × Daniel Cohen | WNO 30 | 2025 | 34 | — |
| 15 | Hayley Carter × Julia Livesey | Polaris 33 | 2025 | 34 | — |
| 16 | Tommy Yip × Reiss Bailey | Polaris 30 | 2024 | 33 | — |
| 17 | Jordan Kirk × Stefan Dabija | Polaris 32 | 2025 | 33 | — |
| 18 | Pavel Jorski × Enrique Sigone | Team BJJ Stars vs Polaris | 2025 | 33 | — |
| 19 | Ffion Davies × Brianna Ste-Marie | ADCC 2022 | 2022 | 32 | sim |
| 20 | Jean Luca Maltese × Mikael Rhaillander | Polaris 36 | 2026 | 31 | — |
| 21 | Chase Vaughn × Dominic Klingher | Polaris 36 | 2026 | 31 | — |
| 22 | Giancarlo Bodoni × Isaque Bahiense | ADCC 2022 | 2022 | 31 | sim |
| 23 | Caius Davies × Rhys Eckley | Polaris 35 | 2026 | 30 | — |
| 24 | Roberto Abreu × Elder Cruz | ADCC 2022 | 2022 | 30 | sim |
| 25 | Diogo Reis × Gabriel Sousa | WNO 20 | 2023 | 28 | — |
| 26 | Pawel Jaworski × Faris Benlamkadem | Polaris 30 | 2024 | 28 | — |
| 27 | Nathan Johnstone × Luiz Finocchio | Polaris 26 | 2023 | 26 | — |
| 28 | Gordon Ryan × Yuri Simoes | ADCC 2024 | 2024 | 26 | sim |
| 29 | Lauren Price × Jessica Evans | Polaris 35 | 2026 | 25 | — |
| 30 | Archer Colaco × Luiz Finocchio | Polaris 29 | 2024 | 25 | — |
| 31 | Craig Jones × Chael Sonnen | CJI 2 - Day 2 | 2025 | 25 | — |
| 32 | Jack Gover × Franck Takoudjou | Polaris 29 | 2024 | 25 | — |
| 33 | Emily Leva × Mary Butterfield | WNO 30 | 2025 | 24 | — |
| 34 | Jack Sear × Romao Carvalho | Polaris 36 | 2026 | 24 | — |
| 35 | Ash Gibson × Arya Esfandmaz | Polaris 34 | 2025 | 22 | — |
| 36 | Mica Galvão × Kenta Iwamoto | WNO 22 | 2024 | 22 | sim |
| 37 | Pavel Jorski × Isaque Bahiense | Polaris 37: Polaris vs BJJ Stars | 2026 | 21 | — |
| 38 | Lucas Kanard × Chris Wojcik | CJI 2 - Day 2 | 2025 | 20 | — |
| 39 | Roosevelt Souza × Haisam Rida | ADCC 2022 | 2022 | 20 | sim |
| 40 | Owen Phillips-Jones × Lloyd Davies | Polaris 35 | 2026 | 20 | — |
| 41 | Bruno Fernandes Rocha × Leandro Henrique dos Santos | <sem evento> | 2020 | 20 | — |
| 42 | Nicholas Donnelly × Jack | Polaris 37 | 2026 | 20 | sim |
| 43 | Jacob Gkatzoflias × Amir Marouani | Polaris 33 | 2025 | 19 | — |
| 44 | Vagner Rocha × Isaac Mitchell | ADCC 2022 | 2022 | 19 | sim |
| 45 | Gita Lowenthal × Bia Mesquita | ADCC 2024 | 2024 | 19 | — |
| 46 | Nicholas Meregali × Yuri Simoes | ADCC 2022 | 2022 | 18 | sim |
| 47 | Amy Campo × Rafaela Guedes | ADCC 2022 | 2022 | 18 | sim |
| 48 | Sean Strickland × Anthony Hernandez | UFC 328 | 2025 | 17 | — |
| 49 | Josh Hinger × Tye Ruotolo | ADCC 2022 | 2022 | 17 | sim |
| 50 | Kyle Chambers × Dory Aoun | WNO 22 | 2024 | 16 | — |
| 51 | Mica Galvão × Lucas Barbosa | CJI 2 - Day 2 | 2025 | 16 | — |
| 52 | Vagner Rocha × Pedro Marinho | ADCC 2022 | 2022 | 15 | sim |
| 53 | Adele Fornarino × Bia Mesquita | ADCC 2024 | 2024 | 15 | sim |
| 54 | Sylvia Nastasa × Kyle Boehm | Polaris BJJ Squads | 2025 | 15 | — |
| 55 | Taylor Pierman × Isaque Bahiense | Polaris 37: Polaris vs BJJ Stars | 2026 | 15 | — |
| 56 | Paige Ivette Climber × Gabby Pagana | WNO 31 | 2025 | 15 | — |
| 57 | Mohammad Abdahanov × Christian Osbach | Polaris 37 | 2026 | 14 | sim |
| 58 | Andrew Tackett × P. Barch | WNO 20 | 2023 | 14 | — |
| 59 | Jozeph Chen × Elijah Dorsey | WNO 24 | 2024 | 14 | sim |
| 60 | Roosevelt Souza × Vince Pizuto | ADCC 2024 | 2024 | 14 | sim |
| 61 | Lucas Barbosa × Josh Hinger | ADCC 2022 | 2022 | 13 | sim |
| 62 | Kevin Beuhring × Chuy Magana | PGF World 2026 | 2026 | 13 | — |
| 63 | Kade Ruotolo × Mica Galvão | ADCC 2022 | 2022 | 13 | sim |
| 64 | Sam Gibbs × Hejraat Rashid | Polaris 30 | 2024 | 12 | — |
| 65 | Gio Martinez × Darragh O'Connail | Polaris 16: Squads 3 | 2021 | 12 | — |
| 66 | Caleb Crump × Armin Bruni | PGF World 2026 | 2026 | 12 | — |
| 67 | Aaron Brooks × Trent Hidlay | NCAA 2024 | 2024 | 11 | sim |
| 68 | Elizabeth Clay × Amy Campo | ADCC 2022 | 2022 | 11 | sim |
| 69 | Helena Crevar × Leilani Bernales | WNO 25 | 2024 | 10 | sim |
| 70 | Gordon Ryan × Patrick Donabedian | EBI 14 | 2025 | 10 | — |
| 71 | Craig Jones × Mason Fowler | <sem evento> | 2019 | 10 | sim |
| 72 | Paulo Costa × Roman Kopylov | UFC 327 | 2025 | 10 | — |
| 73 | Giancarlo Bodoni × Lucas Barbosa | ADCC 2022 | 2022 | 10 | sim |
| 74 | Gordon Ryan × Haisam Rida | ADCC 2022 | 2022 | 10 | sim |
| 75 | Annie Senson × Injana Goodman | Women Who Fight Invitational 2 | 2025 | 9 | sim |
| 76 | Bianca Basilio × Julia Maele | ADCC 2022 | 2022 | 9 | sim |
| 77 | Isaac Trumble × Yonger Bastida | NCAA 2026 | 2026 | 9 | sim |
| 78 | Sergio Vega × Jesse Mendez | NCAA 2026 | 2026 | 9 | sim |
| 79 | Sula Mae Lowenthal × Martina Zola | Women Who Fight Invitational | 2025 | 9 | sim |
| 80 | Landon Robideau × Antrell Taylor | NCAA 2026 | 2026 | 9 | sim |
| 81 | Parker Keckeisen × Dustin Plott | NCAA 2024 | 2024 | 9 | sim |
| 82 | Dante Leon × Diego Pato | WNO 22 | 2024 | 9 | sim |
| 83 | Nathan Orchard × Darragh O'Connail | Polaris 16: Squads 3 | 2021 | 9 | — |
| 84 | Shane Price × Justin Moore | Polaris 34 | 2025 | 9 | — |
| 85 | Mike Perez × Vince Pizuto | ADCC Trials 2024 West Coast | 2024 | 8 | sim |
| 86 | Adam Frank × Las Vegas Kings representative | PGF World 2026 | 2026 | 8 | — |
| 87 | David Carr × Mitchell Mesenbrink | NCAA 2024 | 2024 | 8 | sim |
| 88 | Andy Valenzia × Owain Parry | Polaris 35 | 2026 | 8 | — |
| 89 | Jacob Couch × Sebastian Rodriguez | WNO 22 | 2024 | 8 | sim |
| 90 | Kaynan Duarte × Dean Moody | ADCC 2024 | 2024 | 8 | sim |
| 91 | Helena Crevar × Amanda Pamela Nicole | Polaris 37 | 2026 | 8 | — |
| 92 | Jett Thompson × Jeo Ortiz | PGF World 2026 | 2026 | 8 | — |
| 93 | Dante Leon × Mica Galvão | ADCC 2024 | 2024 | 8 | sim |
| 94 | Craig Jones × Aaron Johnson | EBI 14 | 2017 | 8 | — |
| 95 | Kyle Chambers × Austin Oranday | PGF World 2026 | 2026 | 8 | — |
| 96 | Shane Price × Stephen Abberley | Polaris 32 | 2025 | 8 | — |
| 97 | Daniel Kerkvliet × Felipe Andrew | CJI | 2024 | 8 | sim |
| 98 | Andy Roberts × Yousuf Nabi | Polaris 29 | 2024 | 8 | — |
| 99 | Taylor Pierman × Ruan Alvarenga | Polaris 37: Polaris vs BJJ Stars | 2026 | 8 | — |
| 100 | Carlos Ulberg × Dominick Reyes | UFC 327 | 2025 | 7 | — |
| 101 | Mackenzie Dern × Ffion Davies | CJI | 2024 | 7 | sim |
| 102 | Shawn Melanson × unnamed opponent | PGF World 2026 | 2026 | 7 | — |
| 103 | Dylan Logan × Luca Prattelli | Polaris 37 | 2026 | 7 | sim |
| 104 | Inacio Santos × Felipe Andrew | CJI | 2024 | 7 | sim |
| 105 | Song Yadong × Ricky Simon | UFC 324 | 2025 | 7 | — |
| 106 | Nicholas Meregali × Tye Ruotolo | ADCC 2022 | 2022 | 7 | sim |
| 107 | Max Bickerton × Lew Long | Polaris 25: Absolute GP | 2023 | 7 | — |
| 108 | Dorian Olivarez × Dominic Mahia | ADCC Trials 2023 East Coast | 2023 | 7 | sim |
| 109 | Lucas Barbosa × Levi Jones-Leary | CJI | 2024 | 7 | sim |
| 110 | Travis Haven × Parker Salsbury | PGF World 2026 | 2026 | 7 | — |
| 111 | Felipe Pena × Brandon Reed | ADCC 2024 | 2024 | 7 | sim |
| 112 | Kyle Chambers × Jake Strauss | PGF World 2026 | 2026 | 7 | — |
| 113 | Connor Campbell × Ryan Williams | Polaris 25 | 2023 | 7 | — |
| 114 | Andrew Kochel × Sam Schwartzapfel | PGF World 2026 | 2026 | 7 | — |
| 115 | Craig Jones × Dante Leon | <sem evento> | 2025 | 7 | — |
| 116 | Craig Jones × Dante Leon | <sem evento> | 2018 | 7 | — |
| 117 | Bia Mesquita × Mayssa Bastos | ADCC 2022 | 2022 | 7 | sim |
| 118 | Shawn Melanson × Brett Moyer | PGF World 2026 | 2026 | 6 | — |
| 119 | Roberto Abreu × Elder Cruz | ADCC 2024 | 2024 | 6 | sim |
| 120 | Luke Griffith × Pat Downey | CJI 2 - Day 1 | 2025 | 6 | — |
| 121 | Tye Ruotolo × Levi Jones-Leary | <sem evento> | 2025 | 6 | — |
| 122 | Dan Manasoiu × Mark McQueen | ADCC 2024 | 2024 | 6 | sim |
| 123 | Nick Rodriguez × Andy Varela | ADCC 2022 | 2022 | 6 | sim |
| 124 | Azamat Murzakanov × Aleksandar Rakic | UFC 327 | 2025 | 6 | — |
| 125 | Max Hanson × Daniel Sathler | WNO 22 | 2024 | 6 | sim |
| 126 | Jake Strauss × Sam Schwartzapfel | PGF World 2026 | 2026 | 6 | — |
| 127 | Chewy Magana × Elijah Carlton | PGF World 2026 | 2026 | 6 | — |
| 128 | Dante Leon × Mike Perez | ADCC 2024 | 2024 | 6 | sim |
| 129 | Cam Hurd × Eric | PGF World 2026 | 2026 | 6 | — |
| 130 | Elijah Carlton × Cam Hurd | PGF World 2026 | 2026 | 6 | — |
| 131 | Jake Strauss × Austin Oranday | PGF World 2026 | 2026 | 6 | — |
| 132 | Elijah Carlton × Joshua Squires | PGF World 2026 | 2026 | 6 | — |
| 133 | Giancarlo Bodoni × Damon Ramos | ADCC 2024 | 2024 | 6 | sim |

Ordem = eventos retidos (maior alavancagem primeiro). Lutas com vídeo podem ir
pelo caminho de frames (scripts/frame_pdf.py) em vez de re-refinamento de transcrição.
