pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: root

    required property var sessionState
    required property bool narrow

    readonly property var build: sessionState.build
    readonly property bool hasBuild: build !== null && build !== undefined
    readonly property var focusedCard: build && build.spells.length > 0 ? build.spells[0] : null

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gutter

        RowLayout {
            Layout.fillWidth: true

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                Label {
                    text: "Suggested deck"
                    color: Theme.text
                    font.pixelSize: 22
                    font.bold: true
                }
                Label {
                    text: "Recreate this build in Arena · Draftgoblin remains read only"
                    color: Theme.textMuted
                }
            }

            ComboBox {
                id: pairSelector
                visible: root.hasBuild
                Layout.preferredWidth: 174
                model: root.build ? root.build.pair_options : []
                textRole: "pair"
                Accessible.name: "Deck color pair"
            }

            Button {
                visible: root.hasBuild
                text: "Rebuild"
                onClicked: {
                    const option = pairSelector.currentIndex >= 0
                        ? root.build.pair_options[pairSelector.currentIndex] : null
                    mockProvider.requestBuild(option ? option.pair : "")
                }
            }
        }

        StateBanner {
            Layout.fillWidth: true
            sessionState: root.sessionState
        }

        Rectangle {
            visible: !root.hasBuild
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.surfaceLow
            border.color: Theme.outline
            radius: Theme.radius

            ColumnLayout {
                anchors.centerIn: parent
                spacing: 12

                Label {
                    text: "No deck build available"
                    color: Theme.text
                    font.pixelSize: 20
                    font.bold: true
                }
                Label {
                    text: "Complete or recover a draft to request a suggested deck."
                    color: Theme.textMuted
                }
            }
        }

        Rectangle {
            visible: root.hasBuild
            Layout.fillWidth: true
            Layout.preferredHeight: root.narrow ? 112 : 86
            color: Theme.surfaceLow
            border.color: Theme.outline
            border.width: 1
            radius: Theme.radius

            GridLayout {
                anchors.fill: parent
                anchors.margins: 14
                columns: root.narrow ? 2 : 4
                columnSpacing: 28
                rowSpacing: 6

                Label { text: "PAIR"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true }
                Label { text: root.hasBuild ? root.build.selected_pair : "—"; color: Theme.primary; font.pixelSize: 18; font.bold: true }
                Label { text: "DECK"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true }
                Label { text: root.hasBuild ? root.build.deck_size + " cards" : "—"; color: Theme.text; font.pixelSize: 18; font.bold: true }
                Label { text: "SPELLS"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true }
                Label {
                    text: root.hasBuild && root.build.spell_count !== null
                        ? root.build.spell_count : "—"
                    color: Theme.text
                    font.family: "monospace"
                }
                Label { text: "LANDS"; color: Theme.textMuted; font.pixelSize: 10; font.bold: true }
                Label {
                    text: root.hasBuild && root.build.land_count !== null
                        ? root.build.land_count : "—"
                    color: Theme.text
                    font.family: "monospace"
                }
            }
        }

        RowLayout {
            visible: root.hasBuild
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.gutter

            ScrollView {
                id: buildScroll
                contentWidth: availableWidth
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true

                ColumnLayout {
                    width: buildScroll.availableWidth
                    spacing: 10

                    Label {
                        text: "SPELLS"
                        color: Theme.textMuted
                        font.pixelSize: 10
                        font.bold: true
                        font.letterSpacing: 1.0
                    }

                    Repeater {
                        model: root.build ? root.build.spells : []

                        delegate: Rectangle {
                            required property var modelData

                            Layout.fillWidth: true
                            Layout.preferredHeight: 44
                            color: Theme.surface
                            border.color: Theme.outline
                            border.width: 1
                            radius: Theme.radius

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 12
                                anchors.rightMargin: 12

                                Label {
                                    Layout.preferredWidth: 34
                                    text: "×" + modelData.quantity
                                    color: Theme.primary
                                    font.family: "monospace"
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: modelData.card.name
                                    color: Theme.text
                                    elide: Text.ElideRight
                                }
                                Label {
                                    text: "MV " + modelData.card.mana_value
                                    color: Theme.textMuted
                                    font.family: "monospace"
                                }
                                Label {
                                    text: modelData.letter_grade || "—"
                                    color: Theme.warning
                                    font.bold: true
                                }
                                Label {
                                    text: modelData.score !== null ? "DG " + modelData.score : "DG —"
                                    color: Theme.textMuted
                                    font.family: "monospace"
                                }
                            }
                        }
                    }

                    Label {
                        text: "LANDS"
                        color: Theme.textMuted
                        font.pixelSize: 10
                        font.bold: true
                        font.letterSpacing: 1.0
                    }

                    Repeater {
                        model: root.build ? root.build.lands : []

                        delegate: Label {
                            required property var modelData
                            Layout.fillWidth: true
                            text: modelData.quantity + " " + modelData.name
                            color: Theme.text
                            leftPadding: 12
                        }
                    }

                    Label {
                        text: "BENCH"
                        color: Theme.textMuted
                        font.pixelSize: 10
                        font.bold: true
                        font.letterSpacing: 1.0
                    }

                    Repeater {
                        model: root.build ? root.build.bench : []

                        delegate: Label {
                            required property var modelData
                            Layout.fillWidth: true
                            text: "×" + modelData.quantity + "  " + modelData.card.name
                            color: Theme.textMuted
                            leftPadding: 12
                        }
                    }

                    Rectangle {
                        visible: root.hasBuild && root.build.warnings.length > 0
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
                            text: root.build ? root.build.warnings.join("\n") : ""
                            color: Theme.warning
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            CardPreview {
                visible: !root.narrow
                Layout.preferredWidth: 306
                Layout.fillHeight: true
                recommendation: root.focusedCard
            }
        }
    }
}

