pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: root
    objectName: "buildView"

    required property var sessionState
    required property bool narrow
    required property var displayPreferences
    readonly property var rawBuild: root.sessionState ? root.sessionState.build : null
    readonly property bool hasBuild: root.rawBuild !== null && root.rawBuild !== undefined
    readonly property var build: root.hasBuild ? root.rawBuild : ({
        selected_pair: "",
        pair_options: [],
        spells: [],
        lands: [],
        bench: [],
        deck_size: 0,
        pair_override: null,
        warnings: [],
        spell_count: null,
        land_count: null
    })
    readonly property var automaticPair: {
        for (let index = 0; index < root.build.pair_options.length; index++)
            if (root.build.pair_options[index].automatic)
                return root.build.pair_options[index]
        return null
    }
    readonly property string pairDescription: {
        if (!root.build.pair_override)
            return root.build.selected_pair + " was selected automatically from the drafted pool."
        let automaticPairName = "another pair"
        if (root.automaticPair)
            automaticPairName = root.automaticPair.pair
        return root.build.selected_pair + " was explicitly requested. Automatic evaluation preferred " + automaticPairName + "."
    }
    property var focusedCard: root.hasBuild && root.build.spells.length > 0 ? root.build.spells[0] : null
    property bool spellsExpanded: !root.narrow
    property bool landsExpanded: !root.narrow
    property bool benchExpanded: !root.narrow
    property bool contextExpanded: !root.narrow
    property bool cardDetailsExpanded: false

    onBuildChanged: {
        root.focusedCard = root.hasBuild && root.build.spells.length > 0 ? root.build.spells[0] : null
        root.cardDetailsExpanded = false
    }

    function focusCardDetailsToggle() {
        if (!cardDetailsToggle.visible)
            return
        buildScroll.ScrollBar.vertical.position = 0
        cardDetailsToggle.forceActiveFocus()
    }

    function focusCard(card) {
        root.focusedCard = card
        if (root.narrow && root.displayPreferences.cardPreview) {
            root.cardDetailsExpanded = true
            buildScroll.ScrollBar.vertical.position = 0
            Qt.callLater(function() {
                Qt.callLater(function() {
                    root.focusCardDetailsToggle()
                })
            })
        }
    }

    function manaCount(manaValue) {
        let count = 0
        for (let index = 0; index < root.build.spells.length; index++) {
            const spell = root.build.spells[index]
            const spellManaValue = spell.card ? spell.card.mana_value : null
            if (spellManaValue === null || spellManaValue === undefined)
                continue
            if ((manaValue === 6 && spellManaValue >= 6) || (manaValue !== 6 && spellManaValue === manaValue))
                count += spell.quantity
        }
        return count
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gutter

        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Label { text: "Suggested deck"; color: Theme.text; font.pixelSize: 22; font.bold: true }
                Label {
                    Layout.fillWidth: true
                    text: "Recreate this build in Arena · Draftgoblin remains read only"
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
            }
            ComboBox {
                id: pairSelector
                objectName: "buildPairSelector"
                visible: root.hasBuild
                Layout.preferredWidth: root.narrow ? 116 : 174
                model: root.build.pair_options
                textRole: "pair"
                currentIndex: {
                    for (let index = 0; index < root.build.pair_options.length; index++)
                        if (root.build.pair_options[index].pair === root.build.selected_pair)
                            return index
                    return 0
                }
                Accessible.name: "Deck color pair"
                Accessible.description: "Choose a pair, then rebuild the suggested deck."
            }
            Button {
                objectName: "buildRebuildButton"
                visible: root.hasBuild
                text: "Rebuild"
                Accessible.name: "Rebuild suggested deck"
                Accessible.description: "Requests a new build with the selected color pair override."
                onClicked: {
                    const option = pairSelector.currentIndex >= 0 ? root.build.pair_options[pairSelector.currentIndex] : null
                    sessionProvider.requestBuild(option ? option.pair : "")
                }
            }
        }

        StateBanner { Layout.fillWidth: true; sessionState: root.sessionState }

        Rectangle {
            visible: !root.hasBuild
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.surfaceLow
            border.color: Theme.outline
            border.width: 1
            radius: Theme.radius
            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(parent.width - 40, 440)
                spacing: 12
                Label { text: "No deck build available"; color: Theme.text; font.pixelSize: 20; font.bold: true }
                Label { Layout.fillWidth: true; text: "Complete or recover a draft to request a suggested deck."; color: Theme.textMuted; wrapMode: Text.WordWrap }
                Button {
                    objectName: "buildRequestButton"
                    text: "Request build"
                    Accessible.name: "Request suggested deck build"
                    onClicked: sessionProvider.requestBuild("")
                }
            }
        }

        Rectangle {
            visible: root.hasBuild
            Layout.fillWidth: true
            Layout.preferredHeight: buildSummary.implicitHeight + 28
            color: Theme.surfaceLow
            border.color: Theme.outline
            border.width: 1
            radius: Theme.radius
            GridLayout {
                id: buildSummary
                anchors.fill: parent
                anchors.margins: 14
                columns: root.narrow ? 2 : 4
                columnSpacing: 18
                rowSpacing: 5
                Label { text: "PAIR"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true }
                Label { text: root.build.selected_pair; color: Theme.primary; font.pixelSize: 18; font.bold: true; Accessible.name: "Selected pair " + text }
                Label { text: "DECK"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true }
                Label { text: root.build.deck_size + " cards"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                Label { text: "SPELLS"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true }
                Label { text: root.build.spell_count !== null ? root.build.spell_count : "—"; color: Theme.text; font.family: "monospace" }
                Label { text: "LANDS"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true }
                Label { text: root.build.land_count !== null ? root.build.land_count : "—"; color: Theme.text; font.family: "monospace" }
            }
        }

        ScrollView {
            visible: root.hasBuild
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            id: buildScroll
            contentWidth: availableWidth
            ColumnLayout {
                spacing: 10
                width: buildScroll.availableWidth
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: pairExplanation.implicitHeight + 24
                    color: Theme.surfaceLow
                    border.color: Theme.outline
                    border.width: 1
                    radius: Theme.radius
                    ColumnLayout {
                        id: pairExplanation
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 4
                        Label {
                            text: root.build.pair_override ? "OVERRIDE ACTIVE" : "AUTOMATIC PAIR"
                            color: root.build.pair_override ? Theme.warning : Theme.primary
                            font.pixelSize: 10
                            font.bold: true
                            font.letterSpacing: 1
                        }
                        Label {
                            Layout.fillWidth: true
                            text: root.pairDescription
                            color: Theme.text
                            wrapMode: Text.WordWrap
                            Accessible.name: text
                        }
                    }
                }

                Button {
                    id: cardDetailsToggle
                    objectName: "buildCardDetailsToggle"
                    visible: root.narrow && root.displayPreferences.cardPreview && root.focusedCard
                    Layout.fillWidth: true
                    text: (root.cardDetailsExpanded ? "Hide" : "Show") + " selected card details"
                    Accessible.name: "Toggle selected card details"
                    onClicked: {
                        root.cardDetailsExpanded = !root.cardDetailsExpanded
                        if (!root.cardDetailsExpanded)
                            Qt.callLater(root.focusCardDetailsToggle)
                    }
                }
                CardPreview {
                    objectName: "narrowBuildCardPreview"
                    visible: root.narrow && root.displayPreferences.cardPreview && root.cardDetailsExpanded
                    Layout.fillWidth: true
                    Layout.preferredHeight: 400
                    recommendation: root.focusedCard
                }


                Button {
                    objectName: "buildSpellsToggle"
                    Layout.fillWidth: true
                    text: (root.spellsExpanded ? "Hide" : "Show") + " spells · " + root.build.spells.length + " groups"
                    Accessible.name: "Toggle deck spells"
                    onClicked: root.spellsExpanded = !root.spellsExpanded
                }
                ColumnLayout {
                    visible: root.spellsExpanded
                    Layout.fillWidth: true
                    spacing: root.displayPreferences.compactDensity ? 3 : 6
                    Label { text: "MAIN DECK SPELLS"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true; font.letterSpacing: 1 }
                    Repeater {
                        model: root.build.spells
                        delegate: Button {
                            required property int index
                            objectName: "buildSpellButton" + index
                            required property var modelData
                            readonly property string colors: modelData.card.colors.length > 0
                                ? modelData.card.colors.join(" · ") : "Colorless"
                            readonly property string score: modelData.score !== null && modelData.score !== undefined
                                ? modelData.score : "—"
                            Layout.fillWidth: true
                            Layout.minimumHeight: 40
                            Layout.preferredHeight: root.displayPreferences.compactDensity ? 40 : 48
                            text: "×" + modelData.quantity + "  " + modelData.card.name
                                + " · " + colors
                                + " · DG " + score
                                + " · Grade " + (modelData.letter_grade || "—")
                                + " · " + (modelData.color_fit || "—")
                                + (root.displayPreferences.secondaryStats
                                    ? " · MV " + (modelData.card.mana_value !== null
                                        && modelData.card.mana_value !== undefined
                                        ? modelData.card.mana_value : "—")
                                    : "")
                            Accessible.name: "Focus card " + modelData.card.name
                            Accessible.description: "" + modelData.quantity + " copies, " + colors
                                + ", DG score " + score + ", grade "
                                + (modelData.letter_grade || "—") + ", "
                                + (modelData.color_fit || "—")
                            onClicked: root.focusCard(modelData)
                        }
                    }
                }

                Rectangle {
                    visible: root.spellsExpanded
                    Layout.fillWidth: true
                    Layout.preferredHeight: curveLayout.implicitHeight + 24
                    color: Theme.surface
                    border.color: Theme.outline
                    border.width: 1
                    radius: Theme.radius
                    ColumnLayout {
                        id: curveLayout
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 5
                        Label { text: "MANA CURVE"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true; font.letterSpacing: 1 }
                        Repeater {
                            model: [0, 1, 2, 3, 4, 5, 6]
                            delegate: RowLayout {
                                required property int modelData
                                Layout.fillWidth: true
                                Label { Layout.preferredWidth: 44; text: modelData === 6 ? "6+" : "MV " + modelData; color: Theme.textMuted; Accessible.name: text }
                                Rectangle { Layout.preferredHeight: 12; Layout.preferredWidth: Math.max(2, root.manaCount(modelData) * 18); color: Theme.primary; radius: 2 }
                                Label { text: root.manaCount(modelData); color: Theme.text; font.family: "monospace"; Accessible.name: root.manaCount(modelData) + " cards at mana value " + modelData }
                            }
                        }
                    }
                }

                Button { objectName: "buildLandsToggle"; Layout.fillWidth: true; text: (root.landsExpanded ? "Hide" : "Show") + " lands"; Accessible.name: "Toggle lands"; onClicked: root.landsExpanded = !root.landsExpanded }
                ColumnLayout {
                    visible: root.landsExpanded
                    Layout.fillWidth: true
                    spacing: 4
                    Label { text: "LANDS AND NONBASICS"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true; font.letterSpacing: 1 }
                    Repeater {
                        model: root.build.lands
                        delegate: Label {
                            required property var modelData
                            Layout.fillWidth: true
                            text: modelData.quantity + " " + modelData.name + (modelData.card ? " · drafted nonbasic" : " · basic source " + modelData.source_colors.join("/"))
                            color: Theme.text
                            wrapMode: Text.WordWrap
                            Accessible.name: text
                        }
                    }
                }

                Button { objectName: "buildBenchToggle"; Layout.fillWidth: true; text: (root.benchExpanded ? "Hide" : "Show") + " bench"; Accessible.name: "Toggle bench"; onClicked: root.benchExpanded = !root.benchExpanded }
                ColumnLayout {
                    visible: root.benchExpanded
                    Layout.fillWidth: true
                    spacing: 4
                    Label { text: "BENCH"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true; font.letterSpacing: 1 }
                    Repeater {
                        model: root.build.bench
                        delegate: Button {
                            required property int index
                            objectName: "buildBenchButton" + index
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.minimumHeight: 40
                            text: "×" + modelData.quantity + "  " + modelData.card.name + " · " + modelData.color_fit
                            Accessible.name: "Focus bench card " + modelData.card.name
                            onClicked: root.focusCard(modelData)
                        }
                    }
                }

                Button {
                    visible: root.displayPreferences.detailedBuildContext
                    objectName: "buildContextToggle"
                    Layout.fillWidth: true
                    text: (root.contextExpanded ? "Hide" : "Show") + " pair reasoning and warnings"
                    Accessible.name: "Toggle build reasoning and warnings"
                    onClicked: root.contextExpanded = !root.contextExpanded
                }
                ColumnLayout {
                    visible: root.displayPreferences.detailedBuildContext && root.contextExpanded
                    Layout.fillWidth: true
                    spacing: 6
                    Label { text: "PAIR REASONING"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true; font.letterSpacing: 1 }
                    Repeater {
                        model: root.build.pair_options
                        delegate: Label {
                            required property var modelData
                            Layout.fillWidth: true
                            text: modelData.pair + " · score " + modelData.score.toFixed(1) + " · " + (modelData.playable_count !== null ? modelData.playable_count + " playables" : "playables unavailable") + (modelData.automatic ? " · automatic" : "")
                            color: modelData.selected ? Theme.primary : Theme.textMuted
                            wrapMode: Text.WordWrap
                            Accessible.name: text
                        }
                    }
                    Rectangle {
                        visible: root.build.warnings.length > 0
                        Layout.fillWidth: true
                        Layout.preferredHeight: warningText.implicitHeight + 24
                        color: Theme.warningDark
                        border.color: Theme.warning
                        border.width: 1
                        radius: Theme.radius
                        Label {
                            id: warningText
                            anchors.fill: parent
                            anchors.margins: 12
                            text: root.build.warnings.join("\n")
                            color: Theme.warning
                            wrapMode: Text.WordWrap
                            Accessible.name: "Build warnings: " + text
                        }
                    }
                }
                CardPreview {
                    visible: root.displayPreferences.cardPreview && !root.narrow
                    Layout.fillWidth: true
                    Layout.preferredHeight: 350
                    recommendation: root.focusedCard
                }
            }
        }

    }
}
