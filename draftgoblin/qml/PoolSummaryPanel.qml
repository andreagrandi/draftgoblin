pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root

    required property var pool

    color: Theme.surfaceLow
    border.color: Theme.outline
    border.width: 1
    radius: Theme.radius
    Accessible.role: Accessible.Pane
    Accessible.name: "Draft pool summary"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.panelPadding
        spacing: 12

        RowLayout {
            Layout.fillWidth: true

            Label {
                Layout.fillWidth: true
                text: "POOL SUMMARY"
                color: Theme.textMuted
                font.pixelSize: 10
                font.bold: true
                font.letterSpacing: 1.2
            }

            Label {
                text: root.pool.total_cards + " / 42 cards"
                color: Theme.text
                font.family: "monospace"
                font.bold: true
            }
        }

        Rectangle {
            visible: root.pool.total_cards === 0
            Layout.fillWidth: true
            Layout.preferredHeight: 92
            color: Theme.surface
            radius: Theme.radius

            Label {
                anchors.centerIn: parent
                width: parent.width - 24
                text: "Your pool will appear after the first pick."
                color: Theme.textMuted
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
            }
        }

        GridLayout {
            visible: root.pool.total_cards > 0
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 12
            rowSpacing: 6

            Label { text: "Inferred pair"; color: Theme.textMuted }
            Label {
                text: root.pool.inferred_pair || "Open"
                color: Theme.text
                font.bold: true
            }
            Label { text: "Commitment"; color: Theme.textMuted }
            Label {
                text: Math.round(root.pool.commitment * 100) + "% building"
                color: Theme.warning
                font.family: "monospace"
            }
        }

        Label {
            visible: root.pool.total_cards > 0
            text: "RECENT PICKS"
            color: Theme.textMuted
            font.pixelSize: 10
            font.bold: true
            font.letterSpacing: 1.0
        }

        Repeater {
            model: root.pool.cards ? root.pool.cards.slice(0, 5) : []

            delegate: RowLayout {
                required property var modelData

                Layout.fillWidth: true

                Rectangle {
                    Layout.preferredWidth: 8
                    Layout.preferredHeight: 8
                    radius: 4
                    color: modelData.card.colors.length > 0
                        ? Theme.colorForMana(modelData.card.colors[0]) : Theme.textMuted
                }

                Label {
                    Layout.fillWidth: true
                    text: modelData.card.name
                    color: Theme.textMuted
                    elide: Text.ElideRight
                }

                Label {
                    text: "×" + modelData.quantity
                    color: Theme.text
                    font.family: "monospace"
                }
            }
        }

        Item {
            Layout.fillHeight: true
        }
    }
}

